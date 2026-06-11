# backend/runtime/ — the live machinery a session owns

`sessions.py` (Session/SessionManager + AgentRegistry), `workers.py`
(RunningAgent), `gateway.py`, `bus.py`, `streaming.py`, `tasks.py`
(TaskStore + TaskRunner), `stats.py` (UsageTally).

## Invariants (the *why*)
- **Agents are long-lived background workers** (`RunningAgent`), not request
  handlers: an inbox of prompts, history preserved across runs, state persisted
  continuously. The UI observes and interjects.
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
  models/subprocess. `wiring.py` supplies the real callables.
- **Interjections queue, they don't splice**: Pydantic AI runs a whole
  multi-turn `iter` to completion, so a message submitted mid-run is processed
  right after the current run with full history. To truly interrupt, `stop()`.
