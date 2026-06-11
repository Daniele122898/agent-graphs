# backend/api/ — the HTTP/SSE surface

One module per resource (`sessions`, `teams`, `agents`, `questions`, `stats`,
`tasks`), each exposing `install(app)`; `install_routes` in `__init__.py`
registers them all and `main.create_app` calls it after the lifespan/CORS
setup.

- **Handlers are closures over `app`** reaching state via `app.state.*` —
  never module globals — so tests can run many isolated apps side by side.
  Keep new endpoints in this style.
- **Endpoints stay thin.** Anything beyond resolve→validate→delegate belongs
  in `wiring.py` (orchestration) or the owning component.
- `schemas.py` holds request bodies (wire shapes only) — kept separate from
  `domain/models.py` (the persisted domain shapes) so the wire format can
  evolve without touching the domain spine.
- The history endpoints (`/history`, `/clear`, `/summarize`) 409 while the
  agent is mid-run (mutating history under a run would corrupt it); summarize
  surfaces model failures as a clean 502.
- `/api/stats/models` returns a friendly `{models: [], error}` payload instead
  of a 500 when the model backend is unreachable — the UI must degrade
  gracefully without a local server.
- Task create/retry spawn the runner via `asyncio.create_task` and park the
  handle in `app.state.task_runs` (strong reference — GC would kill the run).
