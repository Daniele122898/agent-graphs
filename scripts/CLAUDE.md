# scripts/ — dev/verification scripts

- `verify_ui.py` — the **browser-driven UI verification harness** (Playwright +
  chromium). It's how UI changes are confirmed in this project (the build alone
  is not enough). It assumes the backend (:8000) and Vite dev server (:5173) are
  running and the DB is fresh, drives the real app through the onboarding →
  control-room → agent-chat → task-board flow, asserts structure, and writes
  screenshots to `/tmp/ag_shots/`. After running it, **Read the PNGs** to
  actually inspect the result.

Run from the repo root: `./.venv/bin/python scripts/verify_ui.py`
(one-time browser install: `./.venv/bin/python -m playwright install chromium`).

When you add UI worth checking, extend this script rather than eyeballing once.
Keep it resilient: wait on elements, not fixed sleeps where avoidable, and never
wait on `networkidle` (the SSE `/events` stream never goes idle).

- `scripted_backend.py` — a backend whose agents run a scripted `FunctionModel`
  (no LM Studio needed). Use it to browser-verify agent *flows* — e.g. the
  ask_user question card — deterministically: start it on :8001, point Vite at
  it with `AG_BACKEND`, and drive with Playwright. Fresh DB every launch.
