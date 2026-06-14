# backend/harness/ — the agent-execution abstraction

The product needs a fixed set of operations from "an agent" (run/interject,
run-to-completion, stop, history/clear/summarize, ask_user list/answer, usage,
delegate). Those are the `Harness` ABC (`base.py`), **keyed by `agent_id`** so
no caller ever holds a harness-specific worker object. A `Session` owns one
`Harness`; the HTTP layer + `wiring.make_task_runner` route everything through
`session.harness`.

- `base.py` — the ABC + `HistoryView` + the pure, shared delegation guards
  (`check_delegation`: neighbor / cycle / depth, raising `ModelRetry`) + a
  concrete `delegate()` (guards → mark asker waiting-on-agent → a2a_message →
  `run_to_completion(target)` → reply) reused by both harnesses.
- `native.py` — `NativeHarness`: thin wrapper over the existing machinery
  (`obtain_worker`/`RunningAgent`, the session's own `QuestionBoard`/
  `UsageTally`/`Gateway`/`registry`). Behavior is byte-for-byte the
  pre-abstraction code. Resolves models via `wiring.resolve_model` AT CALL TIME
  (preserving the `monkeypatch.setattr(wiring,"resolve_model",...)` test seam);
  stateless per session, so one instance is shared by every native session.
- `opencode/` — drives a headless OpenCode server (added in later phases).

## Invariants
- **Universal vs harness-internal Session state**: `bus` (SSE sink) + `registry`
  (lifecycle badges) are UNIVERSAL — both harnesses publish the same event
  names/shapes (user_message, agent_lifecycle, model_request, thinking, text,
  tool_call, tool_result, todos, agent_done, agent_error, a2a_message,
  user_question(/done), task_status, model_wait). `gateway`/`write_lock`/
  `usage`/`questions` are native internals the OpenCode harness leaves idle.
- **Selection**: `SessionManager` builds the harness per session from the
  persisted `sessions.harness` column (default from `config.yml` `harness:` or
  `"native"`); `make_harness(id, ...)` is the factory. The choice survives
  resume.
- **Delegation parity**: native agents delegate through the in-process
  `ask_agent`/`ask_team` tools → `Delegator` (`ask`/`ask_many`);
  `Harness.delegate()`/`delegate_many()` are the parallel paths used by the
  OpenCode `ask_agent`/`ask_team` callbacks — both enforce the same
  `check_delegation` guards. The cross-hop cycle/depth guard requires the
  chain to be threaded across OpenCode's HTTP callback: `run_to_completion`
  stashes the in-flight `delegation_chain` on the agent's `_AgentState`, and
  `/internal/ask_agent` reads it via `OpenCodeHarness.current_chain(asker)` and
  passes it to `delegate()` so the chain accumulates A→B→C (without this the cap
  never accumulates across hops).
- **Delegation accepts a teammate by display NAME or id** (`base.resolve_target`,
  shared by both harnesses): instructions show `Name (\`id\`)`, so models reach
  for the name. Resolution is scoped to the asker's neighbors (resolution set ==
  guard set), trims/strips-backticks/case-insensitive, and runs BEFORE the
  cycle/depth guards (which operate on the canonical id). `check_delegation`
  returns the resolved spec; `delegate`/`Delegator.ask` use `spec.id`.
- **Fan-out (`delegate_many` / `Delegator.ask_many`)**: one agent hands work to
  several teammates AT ONCE (`asyncio.gather` over distinct targets — does NOT
  rely on the model emitting concurrent tool calls, which the weak local model
  won't). Guard violations (unknown/non-neighbor/cycle/duplicate/over-`MAX_FANOUT`)
  fail the whole batch fast (a request mistake, no work done yet); a per-target
  RUNTIME failure is an inline `[consulting X failed: …]` note, never aborting
  siblings. One asker waiting→running transition; `delegate` reuses the same
  lifecycle-free `_consult_one`/`_run_one` per-child runner.

## OpenCode harness specifics (the *why*, hard-won)
- **Custom tools ship as real `.ts` files** under `opencode/tools/`
  (`ask_agent.ts`, `ask_team.ts`), loaded at import into `config.OPENCODE_TOOLS`
  (name → source) and staged into `<repo>/.opencode/tool/` at runtime — not
  embedded as Python strings. `server.py` stages every entry and removes only the
  files it created on shutdown; `<repo>/.opencode/` is gitignored (a runtime
  artifact dir — OpenCode also installs the tools' `node_modules` there). The
  standalone copy under `specs/opencode-smoke/` is an independent spike, NOT used.
- **The pipeline stall was reconfigure-mid-run** (NOT OpenCode concurrency —
  OpenCode runs one Runner per session, no global lock; a delegator parked in
  `ask_agent`'s fetch holds nothing server-side). A graph autosave during a
  delegation chain made the next `_ensure` restart the server and
  `rt.agents.clear()` the `_AgentState` the parked awaiters waited on → hang until
  the run timeout. Fix: `_ensure` SKIPS reconfigure while any agent is busy
  (`_any_busy`: held `st.lock` or `st.busy`), deferring to the next idle run;
  `_reconfigure` frees parked `st.idle` awaiters before clearing (safety net);
  `PUT /api/teams/{id}/graph` is `async` so the `session.graph` swap is serialized
  on the event loop. The single `session.idle` `_final_output` fetch is bounded
  (`wait_for`) so a slow `messages` GET can't starve idle delivery for all agents.
- **Interject steers the live run**: a busy `submit` publishes `user_message` and
  `prompt_async`'s the SAME OC session directly (no `st.lock`, no `st.idle`
  clear) — OpenCode persists the message and re-reads it mid-loop. The old path
  only sent the prompt inside the lock, so an interject blocked silently
  ("vanished"). Never clear `st.idle` from a steer — only the owning run does.
- **Transcript reattaches across a restart**: the OC session id is persisted
  (`agent_state.oc_session_id`) and reattached in `_oc_session` (verified to still
  resolve, else fresh); `history()` after a restart spins the server up and
  reattaches ONLY if a session was persisted (else empty, no spawn). Without this
  a restart wiped the chat (the id lived only in memory). Lifecycle still resets
  to idle on restart (nothing is running then).
- **Server cwd = the repo** (not a config home): OpenCode's `prompt_async` only
  starts a run when the session directory matches the server's project. Config
  is injected via `OPENCODE_CONFIG_CONTENT` (no `opencode.json` in the repo);
  only `<repo>/.opencode/tool/ask_agent.ts` is written and a `.opencode` WE
  created is removed wholesale on shutdown.
- **`session.idle` is the run-complete signal**; a run is bounded by
  `AGENT_GRAPHS_OPENCODE_RUN_TIMEOUT` (default 900s) and the listener frees any
  awaiter if the SSE stream drops — a dead server must fail a run, never hang it.
- **Stop must raise `CancelledError`** out of `run_to_completion` (via the
  `aborting` flag), not return normally, or the TaskRunner would mark a stopped
  task done instead of parking it blocked. The `session.idle` from the abort is
  suppressed (no spurious `agent_done`).
- **Usage**: `input_tokens` is the latest message's full-context size (NOT a sum
  — summing double-counts the re-sent context); `output_tokens` includes
  `reasoning` tokens.
- **A graph/spec edit restarts the server** (`_ensure` compares the graph
  signature → `reconfigure`); the OpenCode-side conversation is lost on restart
  (heavier than native's history carry-forward) — accepted, documented.
- **DeepSeek caveat**: OpenCode's registry may not know newer ids
  (`deepseek-v4-flash`); config declares them under `provider.deepseek.models`,
  but verify live — an unknown id makes `prompt_async` no-op (bounded now by the
  run timeout). The native harness uses DeepSeek directly and is unaffected.
