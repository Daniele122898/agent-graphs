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
- `test_tasks` — state machine, completion gates, revision→blocked cap (runner driven by injected fakes).
- `test_e2e_session` — the **spine**: task → todos → ask_agent → write_file → reviewer gate → done, asserting the REAL file + delegation log + final status, all on FunctionModel.
- `test_runtime` / `test_a2a` / `test_gateway` / `test_history` / `test_streaming` / `test_resume` / `test_multisession` / `test_model_switch` / `test_teams` / `test_graph` / `test_db` / `test_sessions` / `test_main`.

## Live tier (off by default)
`test_live_smoke.py` is gated by `AGENT_GRAPHS_LIVE=1` and hits the real local
model — it verifies what FunctionModel cannot (does a real model actually call
the tools). Slow/flaky by nature; **never** in the fast suite. Use sparingly
(the user's laptop and local model are weak).

> A green deterministic suite proves the machinery (sandbox, gates, state
> machine, delegation) is wired correctly — it says nothing about whether a real
> model picks the right tool. That's the live tier's job; don't let coverage
> create false confidence about real-model behavior.
