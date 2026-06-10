# backend/ — FastAPI + Pydantic AI harness

Python 3.13, FastAPI, Pydantic AI (`pydantic-ai-slim[openai]` 1.x), SQLite
(stdlib). See the root `CLAUDE.md` for the project-wide picture and the
run/test commands.

## Module map (roles, not contents)
- `main.py` — app factory + lifespan + the HTTP/SSE endpoints (thin; delegates glue to `wiring.py`).
- `wiring.py` — the wiring that turns injected-callable abstractions into real agent/reviewer/check runs: `get_or_create_running`, `make_task_runner`, `apply_team_graph`, `resolve_session`, `starter_team_graph`.
- `schemas.py` — HTTP request bodies (wire shapes only), separate from the domain shapes in `models_domain.py`.
- `models_domain.py` — pure Pydantic data shapes (Team/Session/AgentSpec/Capabilities/Task/...). *Data only, no behavior, no I/O.* Named `models_domain` to avoid colliding with `models.py`.
- `models.py` — per-agent **model resolution** (string → Pydantic AI model). Different concern from `models_domain`.
- `db.py` — SQLite schema (teams/sessions/agent_state/tasks/messages) + connection. Owns schema only; table CRUD lives with the component that owns the table.
- `teams.py` / `sessions.py` / `tasks.py` / `agent_state.py` / `a2a.py` — the stores + their domain logic.
- `tools.py` — the dev toolset (sandbox + edit). `capabilities.py` — profile→toolset. `agents.py` — build a Pydantic AI agent. `runtime.py` — `RunningAgent`. `gateway.py` — LLM execution gateway. `streaming.py` — SSE bridge. `history.py` — compaction. `persona.py` / `todos.py` / `stats.py` / `bus.py` / `util.py`.

## Invariants & non-obvious decisions (the *why*)
- **Per-session ownership, never globals.** `Session` (sessions.py) owns its write-lock/gateway/bus/registry/usage. Don't hoist any of these to module scope.
- **The model is INJECTED into every agent** (`build_agent(..., model=...)`). Production wraps it in `GatedModel(model, session.gateway)` so every model call routes through the gateway. Tests inject `FunctionModel`. Never call `resolve_model` inside agent-construction logic that tests need to drive.
- **The gateway gates at the model-call level** (`GatedModel`, a `WrapperModel`), NOT by wrapping whole agent runs — wrapping whole runs would deadlock on `ask_agent` delegation (parent holds the slot while the child waits for it).
- **The edit tool** (`tools.py`): line-range edit + content hash. `read_file` appends an `[edit-token <start>-<end> <hash>]` the model copies into `edit_file` (weak local models can't compute hashes). `effective_range` is the single source of truth shared by numbering and the token hash.
- **Per-agent toolset from the capability profile** (`capabilities.py`): a read-only agent's toolset literally has no write/edit tool. Enforcement is in the tool layer, never in persona prose. Introspect via `ts.tools` (public dict).
- **Streaming uses `agent.iter()`**, not `run_stream_events()`, because `iter` works with plain `FunctionModel` (so the runner is tested with zero tokens). Tool calls/results come from streaming the `CallToolsNode`.
- **`TaskRunner` takes its effectful steps as injected callables** (`run_agent`/`run_reviewer`/`run_check`) so the completion-gate + revision-loop + blocked-on-cap orchestration is tested without models/subprocess. `main.py` supplies the real callables.
- **Delegation guards live in code** (`a2a.py`): target must be a graph neighbor, no cycles (no revisiting the chain), depth capped — each raises `ModelRetry` so the model self-corrects.
- **Changing an agent's model/persona/caps must take effect on the next run.** `RunningAgent` snapshots its built spec (`spec_changed()`); `main._get_or_create_running` rebuilds the cached worker (carrying history) when it changed. Do not cache the agent without this check.
- **No auto-create**: the lifespan rehydrates persisted sessions only; `_session()` requires an explicit `session_id`. There are no default-team endpoints — edit graphs via `/api/teams/{id}/graph`. `_apply_team_graph` syncs a running session's pinned graph only when it's bound to the edited team.

## Testing this folder
`pytest` from the repo root. Tests are deterministic via `FunctionModel` /
`make_sequence_model` (see `tests/CLAUDE.md`). Keep pure logic (sandbox checks,
edit math, state transitions, graph validation) pure so it stays trivially
testable. Side effects (filesystem, model calls, SSE) at the edges.
