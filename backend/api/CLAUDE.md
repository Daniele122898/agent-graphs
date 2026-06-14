# backend/api/ — the HTTP/SSE surface

One module per resource (`sessions`, `teams`, `agents`, `questions`, `stats`,
`tasks`, `providers`, `internal`), each exposing `install(app)`; `install_routes`
in `__init__.py` registers them all and `main.create_app` calls it after the
lifespan/CORS setup.

- `providers.py` — `GET /api/providers` + `GET /api/providers/{id}/models`
  (the Capabilities tab's backend + model pickers).
- `internal.py` — `POST /internal/ask_agent` and `POST /internal/ask_team`, the
  localhost callbacks the OpenCode `ask_agent`/`ask_team` tools POST to.
  Authenticated by a per-session token (`x-ag-token` vs
  `session.harness.token_for(session)`, shared `_authed_session` helper); route
  through the harness's NON-BLOCKING `dispatch`/`dispatch_many` (validate guards
  synchronously → 409 on violation; run the target(s) in the background and inject
  the reply into the asker — the HTTP response is an immediate ACK, not the
  answer), threading the asker's delegation chain via `current_chain` so cross-hop
  cycle/depth caps accumulate. Falls back to blocking `delegate`/`delegate_many`
  if the harness lacks `dispatch`. NOT used by the native harness (its delegation
  is in-process + blocking via `Delegator`).

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
