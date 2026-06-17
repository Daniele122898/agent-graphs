# backend/runtime/ — the live machinery a session owns

`sessions.py` (Session/SessionManager + AgentRegistry), `workers.py`
(RunningAgent), `gateway.py`, `bus.py`, `streaming.py`, `tasks.py`
(TaskStore + TaskRunner), `stats.py` (UsageTally).

## Invariants (the *why*)
- **Agents are long-lived background workers** (`RunningAgent`), not request
  handlers: an inbox of prompts, history preserved across runs, state persisted
  continuously. The UI observes and interjects.
- **`session.graph`/`session.team_id` are swapped through a sanctioned mutator,
  on the event loop.** The opencode harness reads `session.graph` MID-RUN
  (`_ensure`, `reconfigure`, `find_spec`, neighbour lookups), so an off-loop swap
  (a sync `def` handler's threadpool thread) races those reads. Sanctioned
  writers: `SessionManager.rebind` (team switch — async; bundles the in-memory
  swap + the `sessions.team_id` DB write; the API endpoint must NOT touch the
  `sessions` table / `_conn`) and `wiring.apply_team_graph` (graph edit of the
  bound team). BOTH run only from `async` endpoints. Rebind guards
  `require_session_idle` first; the graph-edit path instead DEFERS the opencode
  reconfigure while `_any_busy` — that asymmetry is intentional
  (edit-takes-effect-next-run), not a missing guard. (Shipping rebind as a sync
  `def` with an inline `_conn` UPDATE was the race + layering break that was
  fixed; `tests/test_endpoint_contracts.py` asserts these stay async.)
- **Agent identity for history carry-forward is `(id, name)`, NOT id alone.**
  History persists under `(session_id, agent_id)`, but a graph editor reuses an
  id for a DIFFERENT role (change a node's name/persona, keep its id). Before ANY
  path carries a stored transcript onto a spec it must check the spec is the same
  agent: same id + same name = the same person evolving → keep history through a
  persona/model/caps tweak; same id + different name = a new role on the slot →
  reset (clear messages + drop the opencode `oc_session_id` reattach pointer).
  This holds at EVERY carry point: `obtain_worker` (the get-or-create choke point
  — an in-place rename via `PUT /teams/{id}/graph` rebuilds here, so it must NOT
  carry `prior_messages` across a name change), `SessionManager.rebind`
  (`repurposed_ids`, computed BEFORE the swap; `clear_history` runs AFTER, so the
  agent is in the new graph), and the opencode `_oc_session` reattach (keyed by
  id). A fully-REMOVED id also has its `agent_state` row dropped
  (`AgentStateStore.delete`) so re-adding it later can't inherit stale state.
  Keying identity on id alone is exactly how one role's conversation bled into
  another. (Caveat: `name` doubles as label + identity, so a typo-fix rename
  resets history; a dedicated `slot_uid` column would separate them — recorded
  direction in `log.md`, not worth a migration single-user. A rename across a
  backend restart with no live worker isn't detected (the persisted row has no
  name) — a narrow, documented edge.)
- **One run at a time per worker** (`RunningAgent._run_lock`): an agent is one
  "person" — concurrent work queues rather than interleaving one history. The
  delegation path acquires with a 15-min timeout as a deadlock backstop
  (simultaneous A⇄B mutual delegation); on timeout the asker gets a
  `ModelRetry` saying the target is busy.
- **`stop()` must cancel BOTH run paths**: the inbox loop (`_task`) and an
  in-flight `run_once` (`_current_run` — the task/delegation path). A cancelled
  task run parks the task `blocked` with a "stopped by the user" note
  (TaskRunner catches `CancelledError`, then re-raises) so Retry can revive it —
  never leave it `running`.
- **A failed run still persists its partial transcript**:
  `run_agent_streamed`'s error path fills `history_out` from
  `run.ctx.state.message_history`, and both run paths adopt it in `finally`.
  Without this, the UI reloads an empty history after every failure and the
  agent's work "vanishes".
- **Streaming uses `agent.iter()`**, not `run_stream_events()`, because `iter`
  works with plain `FunctionModel` (zero-token tests). Tool calls/results come
  from streaming the `CallToolsNode`. On error the agent lands in `blocked` and
  an `agent_error` event is published — never a silent failure.
- **The gateway gates at the model-call level** (`GatedModel`, a
  `WrapperModel`), NOT by wrapping whole agent runs — wrapping runs would
  deadlock on `ask_agent` delegation (parent holds the slot while the child
  waits for it). Owned per session: serial mode for low-spec machines.
- **Delegation routes through the target's real `RunningAgent`**
  (`runtime.obtain_worker` is the single get-or-create path shared by HTTP and
  delegation) — never an invisible throwaway agent. That's what makes delegated
  work observable (lifecycle badge, streamed transcript, persisted history).
  Child failures surface on the target as `agent_error` AND to the asker as
  `ModelRetry`.
- **`TaskRunner` takes its effectful steps as injected callables**
  (`run_agent`/`run_reviewer`/`run_check`) so the completion-gate +
  revision-loop + blocked-on-cap orchestration is tested without
  models/subprocess. `wiring.py` supplies the real callables. Each `run_agent`
  call is wrapped in `asyncio.wait_for(task.timeout_hours * 3600)` — a
  **per-task wall-clock budget in HOURS** (default 1.0; `0` = no limit). On
  timeout the agent run is cancelled (the opencode harness aborts the whole
  delegation subtree on that `CancelledError`) and the task parks `blocked`,
  Retry-able. This is distinct from the opencode per-*run* budget
  (`OPENCODE_RUN_TIMEOUT`, which bounds a single hung model run); the task
  budget bounds the whole task incl. nudges + delegation, so keep it ≥ the run
  budget for long single runs.
- **Interjections queue, they don't splice**: Pydantic AI runs a whole
  multi-turn `iter` to completion, so a message submitted mid-run is processed
  right after the current run with full history. To truly interrupt, `stop()`.
