# backend/ — FastAPI + Pydantic AI harness

Python 3.13, FastAPI, Pydantic AI (`pydantic-ai-slim[openai]` 1.x), SQLite
(stdlib). See the root `CLAUDE.md` for the project-wide picture and the
run/test commands. Each package below has its own CLAUDE.md with that
subsystem's invariants — read it before editing there.

## Package map
- `main.py` — app factory + lifespan (boot, rehydrate sessions, orphan-task parking). Entry point: `uvicorn backend.main:app`.
- `wiring.py` — the composition root: turns injected-callable abstractions into real agent/reviewer/check runs (`get_or_create_running`, `make_task_runner`, `apply_team_graph`, `resolve_session`, `starter_team_graph`, history clear/summarize helpers, the open-todos continuation nudge).
- `util.py` — id generation + clock (the only places uuid/wall-clock are read).
- `config.py` — user-local `config.yml` at the repo root (API keys, provider endpoints). **Gitignored — never commit it**; `config.example.yml` is the committed shape. Precedence: env var > config.yml > default.
- `api/` — the HTTP/SSE surface, one module per resource. Wire shapes in `api/schemas.py`.
- `domain/` — pure Pydantic data shapes (`models.py`) + pure graph validation (`graph.py`). No I/O, no behavior.
- `runtime/` — the live machinery a session owns: `sessions.py` (Session/SessionManager), `workers.py` (RunningAgent), `gateway.py`, `bus.py`, `streaming.py`, `tasks.py` (store + TaskRunner), `stats.py` (UsageTally).
- `agents/` — everything that builds one agent: `factory.py` (build_agent), `persona.py`, `capabilities.py`, `tools.py` (DevTools), `todos.py`, `a2a.py` (delegation), `questions.py` (ask_user), `history.py` (compaction + rendering).
- `providers/` — model backends behind one `ModelBackend` abstraction: `base.py`, `lmstudio.py`, `deepseek.py`, `registry.py` (model string → Pydantic AI model; thinking preference → ModelSettings).
- `storage/` — `db.py` (SQLite schema + connections; `DEFAULT_DB_PATH` stays `backend/db.sqlite` — the user's data), `teams.py`, `agent_state.py`. Table CRUD otherwise lives with the component that owns the table (e.g. tasks, message log).

## Cross-cutting invariants (the *why*)
- **Per-session ownership, never globals.** `Session` (runtime/sessions.py) owns its write-lock/gateway/bus/registry/usage/question-board. Don't hoist any of these to module scope.
- **The model is INJECTED into every agent** (`build_agent(..., model=...)`). Production wraps it in `GatedModel(model, session.gateway)`; tests inject `FunctionModel`. Never call `resolve_model` inside agent-construction logic that tests need to drive.
- **No auto-create**: the lifespan rehydrates persisted sessions only; `resolve_session` requires an explicit `session_id`. There are no default-team endpoints — edit graphs via `/api/teams/{id}/graph`. Never reintroduce a hidden default team/session.
- **Anti-stall is two-layered**: prompt guidance (never end a turn with plain-text questions — call `ask_user`; keep working until done/blocked, see agents/persona.py) plus a mechanical check — `wiring.run_agent` re-prompts a task run that ended with open todos, capped at `CONTINUATION_NUDGES`.
- **Changing an agent's model/persona/caps must take effect on the next run.** `RunningAgent` snapshots its built spec (`spec_changed()`); `runtime.obtain_worker` rebuilds the cached worker (carrying history) when it changed. Do not cache the agent without this check.

## Testing this folder
`pytest` from the repo root. Tests are deterministic via `FunctionModel` /
`make_sequence_model` (see `tests/CLAUDE.md`). Keep pure logic (sandbox checks,
edit math, state transitions, graph validation) pure so it stays trivially
testable. Side effects (filesystem, model calls, SSE) at the edges.
