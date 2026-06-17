# Async Delegation: Subtree-Aware Completion (Design Decision)

**Status:** Design only — no code changes. **Author:** Lead architect, agent-graphs. **Date:** 2026-06-17.
**Scope:** the OpenCode harness's non-blocking delegation (`dispatch`/`dispatch_many` in `backend/harness/opencode/harness.py`) and the task-completion gate (`backend/runtime/tasks.py`). The native harness keeps its in-process blocking path (`backend/harness/base.py` `delegate`/`_consult_one`, `backend/agents/a2a.py` `Delegator`) — it already captures full-subtree completion for free and is untouched.

---

## TL;DR — answering Daniele's literal questions

**(1) Would the correlation-id "reply-with-id" design work?** Yes — it is theoretically sound and is a faithful instantiation of **Dijkstra–Scholten diffusing-computation termination detection** (the deficit-counter / acknowledgement-tree form): "a parent replies up only after all its children replied" is the DS *detach rule*, and "the root task is done" is "the initiator has no children and is idle" — the root's deficit returning to zero. It rides the **Actor model** (dormant-between-messages agents woken by a mailbox message) and the **Correlation Identifier** request-reply pattern. The exact pattern is used in production by **A2A** (`taskId` per delegation, terminal state carries the id; the subtree AND-join is left to the implementer), **OpenCode** and **Claude Code** background subagents (child-session-id / agent-id as the correlation id, completion *injected* as a new parent turn), and **OpenAI Responses background mode** (`id` + poll/webhook).

**(2) Is it possible with our async approach?** Yes, it is possible — but it is **not what we should build first**, and the reason is specific to this codebase, not to the theory. The premature-completion bug we actually have is *not* in the async transport: `_run_delegation` already `await asyncio.gather(...)`s all children before injecting their reply (verified at `harness.py:626`). The bug is that the spawned delegation runs as a **detached, un-awaited task** (`_spawn_delegation`, `harness.py:600`) and the task-completion gate (`TaskRunner.run` → `run_for_task` → `run_to_completion`, `tasks.py:228`) returns at the **entry agent's first `session.idle`**, oblivious to that backgrounded subtree. The minimal correct fix is to make the backgrounded subtree *awaitable* and gate task-completion on it — recovering blocking's "subtree-done-for-free" property **without a model-facing `reply` tool**, which means **zero reliance on a weak local model emitting a terminal tool call**. The full correlation-id registry's one unique selling point — durable cross-restart resume — collapses on inspection (a reattached OpenCode session restores the *transcript*, not a *running model loop*; "resume" degrades to "re-prompt," which both designs get equally). **Decision: ship the subtree-await design as v1 now; build the correlation-id registry as v2 only if real usage proves it needed (trigger in §9).**

---

## 1. Verdict and the decision

The four critiques converge, and the codebase research settles it:

- **The user's id design is correct in theory** (Dijkstra–Scholten over Actors with Correlation Identifiers; §2) and is well-precedented (§9 comparison, A2A/OpenCode/Claude-Code research). It would work.
- **But its correctness stakes everything on a weak local model reliably calling a `reply` tool** — and the project's own constraints document this as the *base rate* of failure, not a tail risk (root `CLAUDE.md`: "qwen2.5-coder does NOT tool-call"; small models forget terminal tools). The entire failure-mode kit (watchdog → re-prompt → synthetic-fail) exists to backstop that, and a *false-negative* (work done, reported FAILED because the tool wasn't called) is arguably worse than today's *false-positive*.
- **The id design's headline differentiator — restart durability — is weaker than claimed.** On an OpenCode restart the persisted `oc_session_id` reattaches the *conversation transcript* but **no run is in flight**; the dormant target will never emit `reply`, so the watchdog just times out and synth-fails the whole subtree. To genuinely resume you must **re-prompt** — which the subtree-await design gets identically via the existing reattach + Retry. The "Best vs None" durability row in the naive comparison is wrong (§9).
- **The actual bug is narrow and has a narrow fix** that needs neither a registry, a `reply` tool, nor a `requests` table.

**DECISION — build the Subtree-Await design (v1):**

> Make a delegating agent's backgrounded subtree *awaitable*, and gate **task completion** on the whole subtree, not the entry agent's first idle. Specifically: `dispatch`/`dispatch_many` register their spawned `_run_delegation` task on the **asker's** `_AgentState`; a delegating run's completion (and `run_for_task`) **awaits those child tasks after `session.idle` and after releasing `st.lock`** — so the subtree join happens without holding the per-agent lock or any HTTP fetch open. This is the Dijkstra–Scholten join implemented by *awaiting the coroutine tree* (the awaiting coroutine **is** the deficit counter), exactly as the native blocking path gets it for free — but with the fetch returning immediately so we keep the non-blocking transport that `harness/CLAUDE.md` was built to preserve.

Plus the two product requests, which are **independent of the delegation redesign** and far simpler than the draft implied:
- **#1 per-task timeout (hours, default 1h):** an `asyncio.wait_for` wrap in `TaskRunner.run` + a config field. Harness-agnostic, ~5 lines, zero new state (§7).
- **#2 waiting flag + sustained edge animation:** publish `waiting-on-agent` + `waiting_on` from `dispatch`/`dispatch_many` (the native path already does this), and drive the sustained edge animation off the **universal `agent_lifecycle.waiting_on`** channel — not a new opencode-only event (§3.3, §7).

The correlation-id `RequestRegistry` + `reply` tool is **deferred to v2**, built only on the trigger in §9, and if built, in the **minimal correct form** the critiques converged on: `reply(answer)` with **no model-supplied id** (the harness owns the serving-request pointer), `reply` *mandatory only for join nodes* (deficit > 0) with **idle-auto-complete for leaves**, and the serving-request pointer owned by the registry (not the transient run state).

---

## 2. The model

### 2.1 Agents as dormant actors (the substrate — unchanged)

Each agent owns one persistent OpenCode session (`_AgentState.oc_session_id`, `harness.py:101`). Between messages it is dormant. It wakes when a message lands in its mailbox (`submit`, `harness.py:504`): a delegated task (downward), a child's reply (upward, injected by `_run_delegation`), or a human interjection (steer). This is the Actor model exactly; an LLM agent is the textbook dormant-between-messages actor. **v1 keeps this substrate verbatim** — the change is only *what the harness awaits*, not how agents are woken.

### 2.2 The implicit request tree (v1) vs. the explicit one (v2)

- **v1 (Subtree-Await):** the request tree is **implicit in the coroutine call graph**. When agent A delegates, `dispatch` spawns `_run_delegation(A, [targets])` (`harness.py:600/606`), which `gather`s each target's `run_to_completion` and then injects the combined reply back to A. v1's change: that spawned task is **registered on `A`'s `_AgentState.children`** (a new `set[asyncio.Task]`), and A's task-level completion awaits it. A target that itself delegates spawns its own `_run_delegation`, registered on *its* state — so the join nests transitively, bottom-up, with no counter to keep in sync. The awaiting coroutine tree **is** the deficit.
- **v2 (correlation-id):** a per-`Session` `RequestRegistry` makes the tree explicit (nodes = requests keyed by `req_id`, edges = parent→child), with a **derived** deficit (`count(children where state == OUTSTANDING)`, never a mutated integer) guarded by a per-Session `asyncio.Lock`. Per the spine, never global. Deferred (§9).

### 2.3 Model + request lifecycle state machine

**v1 — per delegating *run* (the unit that completes):** the existing `_AgentState` axes (`busy`/`idle`/`aborting`, `harness.py:104-107`) gain one derived notion, **serving-with-open-children**: a run that reached `session.idle` but whose `children` tasks are not all done.

| State | Meaning | Entered when | Exited when |
|---|---|---|---|
| `RUNNING` | model loop active for this run | `run_to_completion` acquires `st.lock`, `prompt_async` | `session.idle` fires (`harness.py:368`) |
| `IDLE_PENDING_CHILDREN` | run's own turn ended; backgrounded children still running | `session.idle` with `len(children outstanding) > 0` | all `children` tasks resolve |
| `COMPLETE` | own turn done **and** all children resolved | `IDLE_PENDING_CHILDREN` and children all terminal, or `RUNNING`→idle with no children | terminal |
| `BLOCKED` | run errored / first-event watchdog / run-budget timeout | `agent_error`/`session.error`, watchdog (`harness.py:466/480`) | terminal |
| `CANCELLED` | user Stop / shutdown / task-timeout | `stop` (`harness.py:671`), task `wait_for` timeout (§7) | terminal |

The DS conjunction is **(own turn idle) ∧ (all children COMPLETE)**. Crucially, `st.lock` is released at `session.idle` (the `finally` at `harness.py:502`); the children-await happens **after** lock release, so an `IDLE_PENDING_CHILDREN` run does **not** pin the per-agent lock across its subtree (this is the fix to the strongest objection against a naive blocking-in-background approach — see §8 and the §9 comparison).

**Event → transition map (v1):**

| Driving event | Source symbol | Transition |
|---|---|---|
| asker calls `ask_agent`/`ask_team` | `internal_ask_agent`/`_team` → `dispatch`/`dispatch_many` (`internal.py:39/57`, `harness.py:563/575`) | spawn `_run_delegation`; **register the task on `asker.children`**; return immediate ACK |
| target's run idles | `session.idle` (`harness.py:368`) | `RUNNING → IDLE_PENDING_CHILDREN` if children outstanding, else `COMPLETE`; **release `st.lock`** either way |
| all of a run's children resolve | `await asyncio.gather(*children)` completes | `IDLE_PENDING_CHILDREN → COMPLETE`; `_run_delegation` injects combined reply to asker (existing `submit`, `harness.py:639`) |
| target run errors | `agent_error`/`session.error` (`harness.py:348`) | `→ BLOCKED`; child task resolves with an inline failure note (today's `[consulting … failed]`, `harness.py:622`) — parent's join is not wedged |
| run-budget / first-event watchdog | `harness.py:466/480` | `→ BLOCKED`; same as above |
| user Stop | `stop` (`harness.py:671`) | `→ CANCELLED`; **cascade-cancel the run's `children` tasks** (§6) |
| per-task hours timeout | `TaskRunner` `asyncio.wait_for` (§7) | entry run cancelled → `CancelledError` → task parks `blocked` (`tasks.py:228-234`) |

The **v2 request-level** machine (`OUTSTANDING → REPLIED | FAILED | CANCELLED`, terminal = irreversible, mirroring A2A's terminal states and beads `closed`) is documented in the v2 appendix (§10) but not built in v1.

---

## 3. Termination detection

### 3.1 Subtree-completion = the child-task join (v1)

Dijkstra–Scholten's deficit, realized without an explicit counter:

- A delegating run's set of outstanding children **is** its deficit. `_run_delegation` already `await asyncio.gather(*(one(tid, q) …))` over all targets (`harness.py:626`) — that gather is the join barrier; it resolves only when *every* target's `run_to_completion` returns, and a target that itself delegated does not return from its own completion path until *its* children's `_run_delegation` resolves (v1's change makes that true). So the join nests transitively and bottom-up. There is no integer to get out of sync, no double-decrement race (the entire class of counter bugs the termination critique raised against the registry — atomic-increment, derived-vs-mutated, FAILED-then-late-reply double-decrement — **does not exist in v1** because there is no counter and no second async "reply" path; failure of a child resolves its one awaited task with an inline note).
- Decrementing toward zero does **not** auto-complete the parent's *own* work. In v1 this is automatic: the parent's `_run_delegation` injects the children's combined answers back into the parent via `submit` (`harness.py:639`), which re-prompts the parent so it can integrate them and end its turn. The parent's task-level completion is gated on *its* run reaching `COMPLETE` (own turn idle ∧ children done) — the DS conjunction, never inferred from children alone.

### 3.2 Root-task completion — the actual bug, and its fix

**The bug (verified):** `TaskRunner.run` (`tasks.py:216`) calls `self._run_agent(...)` → `run_for_task` → `run_to_completion` (`wiring.py:94`, `harness.py:643`), which returns at the **entry agent's first `session.idle`**. If the entry agent delegated, `dispatch` spawned a *detached* `_run_delegation` (`_spawn_delegation`, `harness.py:600`: `asyncio.create_task` added to `self._bg`, then `add_done_callback(self._bg.discard)` — nothing awaits it). So `run_for_task` returns, `set_result`/`needs_review`/`done` fire (`tasks.py:240-245`) **while the backgrounded subtree is still running**. This is the §A3 gap — *not* a `_run_delegation` defect (that function correctly `gather`s its own children before injecting; the regression test in §11 must target the TaskRunner gate, not `_run_delegation`).

**The fix:**
1. `dispatch`/`dispatch_many` register their spawned task on the **asker's** `_AgentState.children` (new field), instead of an anonymous harness-global `self._bg`.
2. `run_for_task` (`harness.py:643`), after `run_to_completion` returns at the entry agent's idle and the existing open-todos continuation loop, **awaits the entry agent's `children`** (`await asyncio.gather(*entry.children, return_exceptions=True)`) before returning. Because each child's `_run_delegation` itself awaits *its* children, this transitively awaits the whole subtree. **(Implementation note added in review:** for the transitive property to hold, the per-child runner inside `_run_delegation` (`one()`, after `run_to_completion(target)` returns) must *also* await `target_state.children` — the migration in §8 must touch both `_run_delegation.one()` and `run_for_task`, not `run_for_task` alone.**)**
3. No-delegation tasks are unchanged: `entry.children` is empty, the gather is a no-op, `run_for_task` returns at the first idle exactly as today.

This closes root-task completion as the DS root condition ("initiator idle ∧ no outstanding children") **with no model-facing tool and no idle-inference anywhere** — the entry agent finishing its turn is its completion signal (as it is today), but the *task* is not declared done until the awaited subtree resolves.

### 3.3 "Waiting" drives the edge animation; the reply clears it

Today (`useEvents.ts:85-95`) every `a2a_message` flashes the `from→to` edge for **2500 ms** — a brief pulse, not a sustained in-flight state. The native blocking path *does* set `waiting-on-agent` + `waiting_on` around its await (`base.py:239`); the opencode `dispatch` path does **not** (verified — `dispatch` only `_record`s + spawns). That is the entire gap, and it is a two-line omission, not a deep design problem.

v1 fix, on the **universal** channel so the native harness does not regress:
- `dispatch`/`dispatch_many`: at spawn, call `self._lifecycle`/`_set_lifecycle` to publish `agent_lifecycle` for the asker with `lifecycle: "waiting-on-agent"` and `waiting_on: [targets]`. When `_run_delegation` finishes injecting (the asker is woken to integrate), publish `running`.
- **Empty-`waiting_on` stale-badge fix (C11, real):** `_set_lifecycle` drops `waiting_on` when the list is empty (`base.py:334`, `if waiting_on:`), and the frontend only clears `waitingOn` on a non-`waiting-on-agent` lifecycle (`useEvents.ts:71-77`). So we **must publish `running`** (which the frontend clears on) to end the waiting state — never `waiting-on-agent` with `[]`. This already holds for the native path; v1 just mirrors it for opencode.
- **Frontend:** drive the *sustained* edge animation off the existing per-agent `waitingOn` map (already maintained, `useEvents.ts:71-78`) — an asker→target edge animates while `target ∈ waitingOn[asker]`, and clears when the asker leaves `waiting-on-agent`. Keep the 2.5 s `a2a_message` pulse only for the discrete question/reply messages; the `waiting_on`-driven sustained animation **takes precedence** (it is the durable state; the pulse is decorative). **No new `delegation` bus event** — that would break the documented "both harnesses publish the same events" invariant in `harness/CLAUDE.md` since native would never emit it.

This is request #2's acceptance criterion (§7), achieved with zero new events and zero registry.

---

## 4. The model-facing tool surface + how the harness ENFORCES no-premature-reply

### 4.1 v1 — there is no `reply` tool, and that is the point

The tools the model sees are unchanged: `ask_agent`, `ask_team`, `ask_user`, `write_todos` (per-agent toolset, `factory.py:42`). `ask_agent`/`ask_team` still POST to `/internal/ask_agent`/`/internal/ask_team` and return the immediate ACK ("Delegated to X… their reply will be delivered as a follow-up; continue or end your turn", `harness.py:564-573`).

**How no-premature-completion is ENFORCED — by construction, not model discipline:** completion is the agent finishing its turn (`session.idle`), exactly as it is for every run today; the harness then **mechanically** awaits the backgrounded subtree before declaring the *task* done (§3.2). The model literally **cannot** report completion early because there is no "report completion" affordance for it to misuse — the harness owns the join. This is strictly more robust than any prompt-or-tool scheme on a weak model: the #1 documented failure ("forgets the terminal tool") is impossible because there is no terminal tool. This directly answers the prompt's requirement to *not rely on model discipline*: v1 removes the dependence entirely rather than backstopping it.

The one place the model's behavior still matters — a delegating parent that ends its turn *before* integrating the children's answers — is handled by the existing wake: `_run_delegation` injects the answers and `submit` re-prompts the parent, so the parent gets a turn *with* the answers in context and can finish. If it ends that turn without doing anything useful, the existing open-todos continuation nudge (`run_for_task`, `harness.py:643-660`, capped `CONTINUATION_NUDGES = 2`) applies — unchanged.

### 4.2 v2 — if/when the registry is built, the *minimal correct* tool (deferred)

The critiques converged hard here; record the correct shape so v2 isn't relitigated:
- **`reply(answer)` — NO model-supplied id.** The harness knows which request the agent is serving (the serving-request pointer lives in the **registry**, not transient run state — see §10/§D1). Routing correlation through a weak model's echoed id is a *liability* (id-mangling → answer mis-routed to a valid-but-wrong request → silent wrong reply, undetectable by idempotency which only catches re-use). Keep the id in the *prompt* for human-audit transparency; never as a tool argument the model must echo.
- **Mandatory only for join nodes (deficit > 0); leaves auto-complete on idle.** Most delegated agents are leaves and do not sub-delegate. For them, `session.idle`-with-no-children auto-synthesizes the reply from the final assistant text — exactly what blocking does today and what v1 does. The explicit `reply` tool is reserved for a node that must *not* finish before its children, where the harness can also **enforce the barrier** by **suppressing `reply` from the toolset while children are outstanding** (true enforcement — the model cannot emit the call) and, as a fallback, 409-rejecting a premature `reply` with a corrective message (same self-correct loop as the neighbor/cycle guard, `internal.py`). Note honestly: a 409 on a *final* tool call may end the turn on some runners, leaving a deficit-positive parent with no incoming wake — so tool-gating (suppress the tool) is the primary mechanism and 409 is the belt-and-suspenders. This must be verified against the real OpenCode runner before relying on it.
- **One served request per agent** (mirror the native "one run at a time per worker" invariant): a second delegation to a busy agent **queues** rather than handing the model two ids. This removes the multi-id confusion class entirely and is *why* `reply(answer)` can drop the id.

**"Harness-enforced waking" is not a thing — say so plainly.** The harness can *reject* a bad call (negative enforcement) but cannot *compel* a missing one (a leaf that never emits anything). v1 sidesteps this because idle *is* the signal. v2's only lever for a silent leaf is a watchdog re-prompt — which is itself a request to the model, i.e. *not* enforcement. This is why v1's "no terminal tool" design is the robust one and v2 is deferred.

---

## 5. Waking a dormant agent (with a child's reply)

**v1 reuses the existing wake verbatim and it is already correct.** `_run_delegation` `gather`s the children, builds the combined reply, and calls `submit(session, asker_id, combined)` (`harness.py:639`). `submit` (`harness.py:504`) steers a live run if the asker is busy (`prompt_async` on the same session, no lock, no `st.idle` clear, `harness.py:516`) or starts a fresh background run if idle (`harness.py:533`). OpenCode persists conversation context, so the asker resumes with full history.

**Partial-reply policy: wake-once-all-children-done (chosen, and already implemented).** `_run_delegation`'s single `gather` over all targets means the asker is woken **once** with all answers combined — the DS detach barrier in its natural form. This minimizes weak-model turns (one wake, not N), keeps the waiting badge crisp (the asker is continuously `waiting-on-agent` until the single wake — once §3.3 publishes it), and is exactly today's `dispatch_many` combine-and-inject shape (`harness.py:629-635`). For a single `ask_agent`, one-wake and per-reply are identical.

**The wake/serving-context hazards the critiques raised (D1, D3, C2) do not arise in v1** because v1 never introduces a `serving_req_id` on transient run state and never re-derives a "parent request" from agent-global state across wakes. Parenthood in v1 is the coroutine call graph: a target's `_run_delegation` is awaited by the asker's `_run_delegation`/`run_for_task`, so the parent linkage is the await stack, captured at dispatch (`chain` is threaded into the target's run via `delegation_chain → st.chain`, `harness.py:457`, and read by `current_chain`, `harness.py:685` — this still works because it is read *during* the target's run, on the stack, exactly as today). Those hazards are **v2's** to solve (and §10 records that v2 must put the serving pointer on the registry, not `_AgentState`).

---

## 6. Failure modes + mitigations (v1)

| Failure | Mitigation | Where it attaches |
|---|---|---|
| **Forgot-to-reply / silent leaf** | **Cannot happen in v1** — completion is `session.idle` (the run ending its turn), the same signal every run uses; there is no terminal tool to forget. The only re-prompt is the existing open-todos continuation nudge (capped). | `run_for_task` (`harness.py:643`), unchanged |
| **Child run hangs (slow-but-working vs dead)** | The existing per-run guards do this already: **first-event watchdog** (`OPENCODE_FIRST_EVENT_TIMEOUT = 120s`, `harness.py:466` — no event ⇒ abort + error) and **run budget** (`OPENCODE_RUN_TIMEOUT`, default 1h, `harness.py:480`). A hung child's `run_to_completion` raises, `one()` catches it as `[consulting … failed]` (`harness.py:622`), the child task resolves with that note, and the parent's `gather` is **not wedged**. Liveness = streamed events, never silence. | `run_to_completion` watchdogs (existing) |
| **Idempotent replies** | **N/A in v1** — there is exactly one awaited task per delegation and one injection; no second async reply path exists, so no duplicate/late-reply class. (This is a v2-only concern.) | — |
| **Cycles / depth** | Unchanged guard rails: `check_delegation` (`base.py`) runs **synchronously at dispatch** (neighbor/cycle/depth, `MAX_DELEGATION_DEPTH = 3`, `MAX_FANOUT = 4`), threaded across hops via `current_chain` (`harness.py:685`) → `st.chain` — read on the stack during the target's run, so it is reliable in v1 (the D11 "chain source is gone" objection is a *v2* problem, not v1's). | `internal.py` `_chain_for`, `base.check_delegation` (existing) |
| **Restart durability** | **Honest scope:** a restart loses in-flight runs. Persisted `oc_session_id` reattaches the *transcript* (`_session_resolves`, `harness.py:238`), not a running model loop, so nothing resumes on its own. v1 keeps today's behavior: **orphaned tasks park `blocked`** at boot (`main.py:66-76`) and the user presses **Retry** to re-run. This is *the same* restart story the full registry would actually deliver (re-prompt = Retry), at zero cost. We do not claim auto-resume. | `main.py:66` orphan-parking (existing) |
| **Stop / interject** | **Stop** on an agent (`stop`, `harness.py:671`) sets `aborting`, aborts the OC session, frees the awaiter; v1 adds **cascade-cancel of that agent's `children` tasks** (and aborts the descendants' OC runs via `rt.conn.client.abort`, as `stop` does at `harness.py:681`, so cancelled work stops burning tokens — not just a state flip). The entry agent's `run_for_task` then sees `CancelledError` and `TaskRunner.run` parks `blocked` (`tasks.py:228-234`). **Interject** (steer) is orthogonal: it adds a message to a run without touching the children set. | `stop` + new `cancel_children(agent_id)` |

---

## 7. Mapping to the two product requests

### Request #1 — per-task timeout, configurable in **hours**, default 1h

**Harness-agnostic and trivial — decoupled from the delegation redesign.** Add `task_timeout_hours: float = 1.0` to the task/session config (per-task override allowed). In `TaskRunner.run` (`tasks.py:228`), wrap the agent run:

```python
output = await asyncio.wait_for(
    self._run_agent(task.assigned_agent_id, prompt),
    timeout=task.task_timeout_hours * 3600,
)
```

On `TimeoutError`, the **existing** `except Exception` path (`tasks.py:235-238`) parks the task `blocked` with a budget note. Because `_run_agent` → `run_for_task` now awaits the whole subtree (§3.2), `wait_for` bounds the **entire subtree wall-clock**, exactly request #1's intent; the cancellation propagates into the entry run and (via `stop`-style cascade if needed) the children. Native gets the same task timeout for free (it currently lacks one too).

**Reconcile with the existing per-run budget (D9, real):** `OPENCODE_RUN_TIMEOUT` (default 1h, `harness.py:47`) bounds a *single hung run*; `task_timeout_hours` (default 1h) bounds the *whole task/subtree*. They **coexist** with different jobs (one catches a wedged fetch fast, the other caps total task time). To avoid a healthy long task being killed by the per-run budget, document that the **task budget should be ≥ the per-run budget**; since both default to 1h, a single entry run that does ~1h of its own work then delegates would exceed the 1h task budget — so if a user raises `task_timeout_hours`, they should raise `OPENCODE_RUN_TIMEOUT` correspondingly (or we derive the per-run budget from remaining task budget for delegating runs — a v1.1 refinement, noted, not built).

### Request #2 — waiting flag + edge stays animated while a delegation is outstanding

Covered in §3.3: `dispatch`/`dispatch_many` publish `agent_lifecycle waiting-on-agent` with `waiting_on:[targets]` at spawn and `running` when `_run_delegation` injects; the frontend drives the sustained asker→target edge animation off the existing `waitingOn` map and clears it when the asker leaves `waiting-on-agent`. Empty-`waiting_on` stale-badge fixed by always publishing `running` to end the state. No new events; native parity preserved; the "⏳ waiting on X" badge (`AgentNode.tsx:47-51`) now lights for opencode delegation too.

---

## 8. Migration, file by file (v1)

Grounded in verified symbols. The native harness (`base.py` `delegate`/`delegate_many`, `a2a.py` `Delegator`, `factory.py:42` toolset) is **untouched** except it already emits `waiting_on` (no change needed).

**`backend/harness/opencode/harness.py`:**
- `_AgentState` (`:99`): add `self.children: set[asyncio.Task] = set()` — the asker's outstanding delegation tasks.
- `_spawn_delegation` (`:600`): register the created task on the **asker's** `st.children` (and discard on done), instead of the anonymous harness-global `self._bg`. So each asker owns its outstanding subtree handles.
- `dispatch`/`dispatch_many` (`:563/:575`): after `_spawn_delegation`, publish `agent_lifecycle waiting-on-agent` + `waiting_on:[targets]` for the asker (via `self._lifecycle`/`_set_lifecycle`). Unchanged otherwise (synchronous guard validation + immediate ACK; the ACK text already says "do NOT wait inline").
- `_run_delegation` (`:606`): the `gather`/inject over its direct children is correct; **add** (per the §3.2 implementation note) an `await asyncio.gather(*target_state.children, …)` for each target after its `run_to_completion` returns, so a nested delegate's own subtree is awaited before this level injects. On finishing injection, publish the asker's lifecycle back to `running` (ends the waiting state per §3.3, C11 fix). Keep the existing failure-note handling (`harness.py:622`).
- `run_for_task` (`:643`): after the existing `run_to_completion` + open-todos continuation loop, **await the entry agent's children**: `await asyncio.gather(*entry_state.children, return_exceptions=True)` before returning. This is the root-task subtree gate (§3.2). Empty children ⇒ no-op ⇒ no-delegation path unchanged. **Note the lock ordering:** `run_to_completion` releases `st.lock` in its `finally` at `:502` *before* `run_for_task` awaits children — so the subtree-await does **not** hold the entry agent's lock (the §9 anti-pin property).
- `stop` (`:671`): add cascade — cancel the agent's `st.children` tasks and abort descendants' OC runs (`rt.conn.client.abort`).

**`backend/runtime/tasks.py`:**
- `TaskRunner.run` (`:216/:228`): wrap `self._run_agent(...)` in `asyncio.wait_for(..., timeout=task.task_timeout_hours * 3600)`; `TimeoutError` falls into the existing `except Exception` → `blocked` (`:235`). Add `task_timeout_hours` to the task domain object + store schema (additive, nullable, defaulting 1.0).

**`backend/wiring.py`:** `make_task_runner.run_agent` (`:94`) is unchanged — it already calls `run_for_task`, which now transitively awaits the subtree. (No new seam needed; the gate lives in the harness, which is the right altitude.)

**Bus events:** **none new.** Reuse `agent_lifecycle` + `waiting_on` (universal, both harnesses). This is the deliberate divergence from the draft, which proposed an opencode-only `delegation` event that would have broken the harness-parity invariant in `harness/CLAUDE.md`.

**Frontend:**
- `useEvents.ts`: no event-registration change. The sustained edge animation is derived from the existing `waitingOn` map (`:71-78`): an edge `asker->target` is "active/animated" while `target ∈ waitingOn[asker]`. Keep the 2.5 s `a2a_message` pulse for discrete messages; `waitingOn`-derived state takes precedence for the sustained look.
- `Canvas.tsx` (`:49-61`) / `AgentNode.tsx` (`:47-51`): edge `animated` keyed off the union of the pulse set and the `waitingOn`-derived set; the "⏳ waiting on X" badge already reads `waitingOn` — it now lights for opencode delegation because §3.3 publishes it.

**Config:** add `task_timeout_hours` to the session/task config shape (`config.example.yml` + `backend/config.py` if surfaced there) and the tasks table.

*(No `requests.py`, no `requests` table, no `reply.ts`, no `/internal/reply`, no `RequestRegistry`, no `serving_req_id` in v1. Those are §10 v2.)*

---

## 9. Honest comparison + recommendation

| Dimension | **Blocking + Phase A** (revert to in-fetch blocking `delegate`/`delegate_many` with the shipped per-request HTTP timeouts/watchdog/retry) | **Subtree-Await (v1, RECOMMENDED)** (non-blocking transport; register child tasks on the asker; await the subtree at the task gate, *after* lock release) | **Correlation-id registry (v2)** (explicit `RequestRegistry`, `reply` tool, `requests` table, watchdog, durability) |
|---|---|---|---|
| **Correctness (no premature task completion)** | Correct by construction — the in-fetch await *is* the join | **Correct** — the child-task gather *is* the join; the awaiting coroutine tree is the deficit; no counter, no model cooperation | Correct *if* the model emits `reply` (or idle-auto-complete for leaves, §4.2); needs the watchdog/derived-deficit kit to hold |
| **Model-reliability risk** | None (no reply tool) | **None** (no reply tool; completion = idle, the existing signal) | **Highest** — relies on a weak model calling a terminal tool; entire §6-style kit is the backstop; risk of *false-negative* completion |
| **Held-fetch fragility (Bun ~255s, orphaned subtrees)** | **Reintroduces it** — the whole subtree runs inside one HTTP `ask_agent` fetch | **Avoided** — each hop is one short fetch returning an ACK; the subtree runs as background coroutines | Avoided |
| **Per-agent lock pin / stacked per-hop caps** | Pins nested locks for the whole subtree; per-hop caps stack | **Avoided** — `st.lock` is released at `session.idle`; children are awaited *after* release, so an `IDLE_PENDING_CHILDREN` run holds no lock (this is the key fix vs. a naive blocking-in-background hybrid) | Avoided |
| **Robustness, deep/long tasks (depth ≤3, fanout ≤4)** | Fragile (held fetch + lock pin) | **Best** — nothing held open; bounded tree (`MAX_DELEGATION_DEPTH=3`, `MAX_FANOUT=4`) | Best transport; adds reliability risk |
| **Restart durability** | None mid-flight | **Transcript reattaches; task re-driveable via Retry** (orphan-park, `main.py:66`) | **Same in practice** — reattach restores transcript not a running loop; "resume" = re-prompt = Retry. The registry does **not** auto-resume a frozen run; its durability differentiator is largely illusory |
| **Observability (waiting/edge)** | Good (native-style waiting works) | **Good** — `waiting_on` on the universal channel; sustained animation; native parity preserved | Best (per-request audit) — at the cost of an opencode-only event unless carefully kept on the universal channel |
| **Implementation cost** | Low (revert) — but reintroduces documented fragility | **Low** — `children` set, one gather in `run_for_task`, a `wait_for`, a `waiting_on` publish, a frontend tweak; **no new tool/table/registry** | **Highest** — new module, table, tool, watchdog, locking discipline, durability, frontend |

### RECOMMENDATION

**Build Subtree-Await as v1 now. Defer the correlation-id registry to v2.**

Rationale, decisively:

1. **The bug is narrow and v1 fixes it exactly.** The premature completion is the un-awaited backgrounded subtree at the TaskRunner gate (`tasks.py:228` returns at the entry agent's first idle while `_spawn_delegation` runs detached). Registering child tasks on the asker and awaiting them in `run_for_task` recovers blocking's "subtree-done-for-free" property **without** a model-facing tool and **without** the held-fetch/lock-pin fragility — because the await happens *after* `st.lock` is released. This is strictly better than both the pure-blocking revert (which reintroduces the documented fragility) and a naive "blocking-in-background hybrid" (which would pin the lock across the subtree — the explicit non-goal here).
2. **It removes model-reliability risk entirely**, rather than backstopping it. On the project's documented weak local models, "forgets the terminal tool" is the *base rate*; v1 has no terminal tool to forget. This is the single most important property for a single-user local tool.
3. **The id design's unique selling point evaporates on inspection.** Restart "resume" is really re-prompt, which v1 gets via Retry; the bounded tree (depth 3, fanout 4) does not need durable server-side correlation. Per-request audit is nice-to-have, not needed to fix the bug.
4. **Both product requests are independent and cheap** (#1 = `asyncio.wait_for` + config; #2 = publish `waiting_on` + a frontend tweak), so they ship in v1 regardless of the delegation internals.

**Pure blocking is not the destination** — keep it only as the *native* harness path (in-process awaits are cheap and already correct there). On opencode it reintroduces the Bun-fetch/lock-pin fragility `harness/CLAUDE.md` was written to avoid.

**Trigger to build v2 (correlation-id registry):** build it only when real dogfooding shows a concrete need that v1 cannot meet — specifically **(a)** a requirement to *auto-resume* an interrupted background subtree across a server restart *without* a human pressing Retry (e.g. unattended overnight runs that must survive a crash), or **(b)** a need for partial/streaming progress from independent long-running children (wake-per-reply) rather than a single join, or **(c)** per-request audit/observability that the implicit coroutine tree cannot surface. Until one of those is real and observed, v2 is speculative complexity. If built, use the minimal correct form (§10): `reply(answer)` with no model id, leaves auto-complete on idle, `reply` mandatory + tool-gated only for join nodes, one served request per agent, registry-owned serving pointer, derived deficit under a per-Session lock, success-and-failure replies both close the edge, and `rehydrate` that **re-prompts** (not merely re-arms a timer).

---

## 10. v2 appendix — the correlation-id registry, done right (deferred, not built)

Recorded so v2 isn't relitigated; built only on the §9 trigger.

- **`backend/runtime/requests.py` — `RequestRegistry` (per-Session, never global).** `dict[req_id, Request]`; **derived** deficit (`count(children where OUTSTANDING)`, recomputed on each child terminal transition — never a mutated `+=/-=` integer, which kills the C1/C4/C5 increment-ordering and double-decrement races). Every deficit recompute + paired state transition happens inside **one non-awaiting critical section** under a per-Session `asyncio.Lock`; bus/submit I/O happens *after* the mutation, never interleaved. Owns the **serving-request pointer** per agent (set on every wake, cleared only on `reply` — *not* on `_AgentState`, which is cleared per-run and goes stale across the multiple wakes of a long-lived request: the D1 fix).
- **Request lifecycle:** `OUTSTANDING → REPLIED | FAILED | CANCELLED`, terminal = irreversible (mirrors A2A terminal states, beads `closed`). A re-entrant delegation onto an in-flight node mints a fresh `req_id` (distinct edge), but the cycle guard refuses A→…→A before creation.
- **`reply(answer)` tool — no model id** (§4.2). Mandatory + **tool-gated** (suppressed from the toolset while deficit > 0) only for join nodes; leaves auto-complete on `session.idle` (the existing signal). 409-reject is the fallback, *after* verifying the OpenCode runner doesn't end the turn on a failed final tool call.
- **One served request per agent**: a second delegation to a busy agent **queues** (mirrors the native one-run-per-worker invariant), removing the multi-id confusion surface.
- **Wake delivery:** start a **fresh tracked run** for a parent re-woken to integrate (never a `submit` steer into a winding-down run — the C2/D3 fix); reconcile the new serving-with-open-children state with `is_busy`/`_any_busy`/the watchdog.
- **Watchdog = liveness, not a flat deadline** (C7/D4): escalate to FAILED only after a heartbeat window with *no streamed event* (reuse `OPENCODE_FIRST_EVENT_TIMEOUT`/`message.part.updated`), not a per-hop timer; ancestor deadline ≥ subtree critical path. The synthetic-timeout reply carries the model's **actual last output** as a best-effort answer (so completed-but-unreported work isn't discarded as a bare failure — the D5 false-negative fix).
- **Cancellation is edge-scoped** (`cancel_subtree(req_id)`, not agent-scoped — the C6 fix, so a re-entrant node's *unrelated* computation isn't killed), aborts descendants' OC runs, and resolves the root future before `CancelledError` unwinds.
- **Durability is honest:** `rehydrate` must **re-prompt** each outstanding target (transcript-as-context continuation), not merely re-arm a timer (a reattached session has no running loop — D4). Sequence `rehydrate` vs. `main.py:66` orphan-parking explicitly: simplest correct choice is **restart parks `blocked`; Retry re-drives** (matching v1 and today's UX), and the registry is rebuilt as an audit/observability layer, not an auto-resumer.
- **`messages` table migration** (if used for audit): `req_id`/`parent_req_id` columns are **additive/nullable**, back-compatible with pre-existing rows; `MessageLog.record`/`for_session` (`a2a.py:87/95`) updated to name columns explicitly so old rows still read.
- **Storage:** `_reconfigure` (graph edit, `harness.py:191`) must resync the surviving Session-owned registry against the cleared `_AgentState`s (mark requests on dropped agents FAILED/CANCELLED) — listed because the draft omitted it.

---

## 11. Test plan

**Deterministic fake-opencode tests** (existing pattern: `connect` injected into `OpenCodeHarness.__init__`, `harness.py:141`; a fake `Connection` emits chosen SSE events; token-free).

**v1 (must pass now):**
1. **Single-hop, no sub-delegation** — Lead→Planner; Planner idles after one turn. Assert: `run_for_task` returns at idle, `entry.children` empty, task `done`. (Baseline — no-delegation path unchanged.)
2. **Two-level subtree — THE regression test, correctly targeted.** Lead(entry)→Planner→{Backend, Frontend}. Script: Planner's run dispatches both children then idles; children idle after their turns. **Assert it FAILS on today's code** (task marked `done`/`needs_review` at the entry agent's first idle while `_spawn_delegation` runs detached — `tasks.py:228` returns early) **and PASSES after** (`run_for_task` awaits `entry.children` → the children's `_run_delegation` → their `run_to_completion`; task reaches `needs_review`/`done` **only after** the full subtree resolves). Note: the bug is the **TaskRunner gate**, not `_run_delegation` (which already `gather`s its own children) — the test asserts on task status timing, not on Planner's run completing.
3. **Lock-not-pinned** — assert that while Planner is `IDLE_PENDING_CHILDREN`, `is_busy(Planner)` reflects the lock being **released** (a steer/interject to Planner does not deadlock behind the subtree). Guards the §9 anti-pin property.
4. **Child run fails/hangs** — a child's `run_to_completion` raises (watchdog/run-budget); assert `one()` records `[consulting … failed]`, the child task resolves, the parent's `gather` completes, the task lands `needs_review` with the failure note inline (no hang).
5. **Cycle/depth** — Lead→Planner→Lead refused at `check_delegation` (409); depth cap accumulates across hops via `current_chain` (read on the stack during the target's run).
6. **Stop cascade** — Stop Planner mid-subtree; assert `st.children` cancelled, descendants' OC runs aborted, entry `run_for_task` sees `CancelledError`, task parks `blocked` (`tasks.py:228-234`).
7. **Per-task hours timeout** — set `task_timeout_hours` tiny; assert `asyncio.wait_for` fires, entry run cancelled, task `blocked` with budget note via the existing `except` (`tasks.py:235`).
8. **Waiting flag + sustained edge** — assert `dispatch` publishes `agent_lifecycle waiting-on-agent` with `waiting_on:[target]` for the asker, the asker stays `waiting-on-agent` until `_run_delegation` injects, then a `running` lifecycle clears it (C11 empty-`waiting_on` regression: assert the badge actually clears). Drives request #2.
9. **Native parity** — a native-harness delegation still emits `waiting_on` and the frontend sustained animation works identically (no `delegation`-event dependency).

**v2 (added with the registry, tagged):** 409/tool-gated premature reply; idle-auto-complete leaf vs. mandatory `reply` join node; liveness-watchdog synth-fail carrying last output; idempotent first-wins + FAILED-then-late-reply (no double-effect); edge-scoped cancel; `rehydrate` re-prompts and re-links `task_id`; `_reconfigure` registry resync.

**Live DeepSeek E2E** (env-gated, `AGENT_GRAPHS_LIVE=1`, alongside `tests/test_live_smoke.py`): a real OpenCode + DeepSeek 3-agent team (Lead→Planner→{Backend,Frontend}) on a throwaway repo, with a task that *forces* Planner to delegate to both. Assert the task reaches `done` **only after** all four agents have idled and the subtree gather resolved, the Lead's final answer references both children's work, and the canvas edges stayed animated (poll `agent_lifecycle`/`waiting_on`) for the whole subtree. End-to-end proof completion is no longer premature on a real weak model — **with no `reply` tool, so no model-discipline dependence.**

**Frontend:** extend `scripts/verify_ui.py` to drive an opencode delegation and screenshot the sustained edge animation + "⏳ waiting on X" badge while the delegation is outstanding, then confirm both clear on the reply injection (request #2 acceptance).

---

### Key file references (verified)
- `backend/harness/opencode/harness.py` — `dispatch`/`dispatch_many` (`:563`/`:575`), `_spawn_delegation` (`:600`, detached task — **the un-awaited handle**), `_run_delegation` (`:606`, already `gather`s children at `:626` — **NOT the bug**), `run_to_completion` (lock release in `finally` `:502`, watchdogs `:466`/`:480`), `submit` wake (`:504`, steer `:516` / fresh `:533`), `run_for_task` (`:643`, **where the subtree gate goes**), `current_chain` (`:685`), `_AgentState` (`:99`, gains `children`), `session.idle` handler (`:368`), `stop` (`:671`, gains cascade).
- `backend/runtime/tasks.py` — `TaskRunner.run` (`:216`/`:228`, **the real premature-completion gate** + the `wait_for` timeout site; `except` → `blocked` `:235`).
- `backend/harness/base.py` — `delegate`/`delegate_many`/`_consult_one` (`:221`–), `_set_lifecycle` (`:329`, empty-`waiting_on` guard at `:334` — **C11**), `_record` (`:338`), `check_delegation`.
- `backend/wiring.py` — `make_task_runner.run_agent` (`:94`, unchanged seam).
- `backend/main.py` — orphan-parking on boot (`:66`–`:76`, the restart story).
- `backend/agents/a2a.py` — native `Delegator` (untouched), `MessageLog` (`:80`/`:87`/`:95`).
- `frontend/src/hooks/useEvents.ts` — `agent_lifecycle`/`waiting_on` (`:71`–`:78`, already correct), `a2a_message` 2.5 s pulse (`:85`–`:95`); `Canvas.tsx` (`:49`–`:61`), `AgentNode.tsx` (`:47`–`:51`).
