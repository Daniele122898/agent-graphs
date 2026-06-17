# tests/ — deterministic, behavior-first

`pytest` from the repo root (`./.venv/bin/python -m pytest`). `asyncio_mode =
auto` (async tests need no decorator). The whole suite is **token-free and
needs no model server** — it MUST stay that way and stay green.

## Philosophy (YOU MUST follow)
- **Test behavior, never constants or framework internals.** The litmus test: *if it fails, does that mean something is broken, or merely changed?* Only "broken" earns a test. No `assert MAX_DEPTH == 3`, no trivial getters.
- **Weighted toward function + end-to-end tests** of real behavior over tiny units, per the project's design constraint.
- **Determinism, zero tokens.** Drive agents/sessions with a scripted fake model — never a real network call in the fast suite.

## The key seams
- `make_sequence_model([...turns])` (in `conftest.py`) — a `FunctionModel` that emits a scripted sequence of responses (one per model call). This is what makes whole agent/session runs testable with no LLM. Structured output (e.g. reviewer `ReviewVerdict`) is scripted by calling `info.output_tools[0].name`.
- `bootstrap_session(client, repo_path, ...)` — explicit team-create + session-launch for API tests (the app no longer auto-creates anything; tests must set this up).
- Fixtures: `conn` (temp SQLite, schema-initialized), `repo` (temp dir), `fake_clock` (deterministic, ordered timestamps — inject it into stores).
- `create_app(db_path=...)` takes **only** a db_path now (no repo_path); use `TestClient(app)` to exercise the real lifespan.

## What the suites cover (so you know where to add)
- `test_tools` / `test_capabilities` — sandbox path/glob enforcement, stale-hash edit reject, profile→toolset shape.
- `test_tasks` — state machine, completion gates, revision→blocked cap, and the **per-task timeout** (hours → `asyncio.wait_for`: a slow run is cancelled + parked blocked; `0` = no limit) — runner driven by injected fakes.
- `test_e2e_session` — the **spine**: task → todos → ask_agent → write_file → reviewer gate → done, asserting the REAL file + delegation log + final status, all on FunctionModel.
- `test_questions` — ask_user end-to-end over the HTTP API (run parks, answer resumes it, answers reach the model) + the open-todos continuation nudge.
- `test_runtime` / `test_a2a` / `test_gateway` / `test_history` / `test_streaming` / `test_resume` / `test_multisession` / `test_model_switch` / `test_teams` / `test_graph` / `test_db` / `test_sessions` / `test_main`.
- Endpoint tests that need a real (scripted) agent run monkeypatch `backend.wiring.resolve_model` with `make_sequence_model` and poll via `TestClient` (the lifespan portal runs background tasks).
- `test_harness` / `test_providers` / `test_opencode_config` — the harness abstraction seam, the model-backend providers, and OpenCode config generation (pure).
- `test_endpoint_contracts` — **route-table backstops** so a new mutating endpoint can't silently skip the guard the rebind endpoint first missed: asserts the `session.graph` mutators (`POST /sessions/{id}/rebind`, `PUT /teams/{id}/graph`) are `async`, and that every POST/PUT under `/api/session(s)|/api/agent` is classified GUARDED (busy-guarded via `wiring.require_*_idle`) or EXEMPT — a new unclassified route FAILS the test, forcing the guard decision. The prose contract lives in `backend/api/CLAUDE.md`. When you add such a route, classify it there.
- `test_opencode_harness` / `test_internal` / `test_opencode_e2e` — the OpenCode harness driven by **`_fake_opencode.py`**, a deterministic in-process fake server (NO LLM, NO subprocess): scripted "turns" emit SSE parts ending in `session.idle`, plus park/error/question turns. Covers run/stream/history/usage/stop/nudge/reviewer, ask_user park-resume, delegation + guards, the `/internal/ask_agent` + `/internal/ask_team` endpoints, reconfigure-on-edit, error/abort/stream-death paths, and a full API-level task run. Also the OpenCode bug-cluster fixes (2026-06-14): **stall** (reconfigure-skipped-while-busy + `_reconfigure` frees parked awaiters — `_opencode_session(..., graph=)` takes a custom graph), **interject** (a busy `submit` steers a 2nd `prompt_async` into the live run), **fan-out** (`delegate_many` parallelism + per-target failure isolation + guards), **name↔id** delegation (`resolve_target`, native + the callback), **reattach durability** (simulate a restart: new harness + same state store + the fake's message map carried forward → `history()` reattaches the persisted OC session), and **subtree-aware delegation** — the fake's `on_delegate` hook lets a scripted turn trigger a real `dispatch` mid-run (modelling the `/internal` callback), so the regression proves a nested delegate's reply is its FINAL integrated answer (not a premature first turn) and the task completes only after the whole subtree, plus the asker's `waiting-on-agent` lifecycle on dispatch clears once the reply lands. The OpenCode harness has no live tier in the fast suite; live verification is manual (the binary + a model), like `test_live_smoke`.

## Live tier (off by default)
`test_live_smoke.py` is gated by `AGENT_GRAPHS_LIVE=1` and hits the real local
model — it verifies what FunctionModel cannot (does a real model actually call
the tools). Slow/flaky by nature; **never** in the fast suite. Use sparingly
(the user's laptop and local model are weak).

> A green deterministic suite proves the machinery (sandbox, gates, state
> machine, delegation) is wired correctly — it says nothing about whether a real
> model picks the right tool. That's the live tier's job; don't let coverage
> create false confidence about real-model behavior.
