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
- **Delegation parity**: native agents still delegate through the in-process
  `ask_agent` tool → `Delegator` (unchanged); `Harness.delegate()` is the
  parallel path used by the OpenCode `ask_agent` callback — both enforce the
  same `check_delegation` guards, so behavior is identical across harnesses.
