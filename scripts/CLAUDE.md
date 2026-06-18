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

- `verify_opencode_ui.py` — the **live** OpenCode-harness browser E2E. Unlike
  `verify_ui.py` (native, dead-model, structure-only), it needs LM Studio with a
  small tool-capable model (qwen/qwen3-1.7b), the `opencode` binary, and a
  backend started with `AGENT_GRAPHS_CALLBACK_URL` pointing at itself. It drives
  the real UI (onboarding → launch an **opencode** session → run a task) and
  asserts the agent did real work (created `hello.txt`) with the run visible in
  the transcript + the "opencode harness" chip. Run a backend (real opencode
  harness) + Vite, then `AG_UI_URL=… verify_opencode_ui.py`. Verified passing
  2026-06-13 (qwen3-1.7b wrote the file via OpenCode's `write` tool).
- `scripted_backend.py` — a backend whose agents run a scripted `FunctionModel`
  (no LM Studio needed). Use it to browser-verify agent *flows* — e.g. the
  ask_user question card — deterministically: start it on :8001, point Vite at
  it with `AG_BACKEND`, and drive with Playwright. Fresh DB every launch.
- `verify_oc_reattach.py` — focused **live** durability check (the Step-6 restart
  reattach): spawns a real `opencode serve` on a temp repo, creates a session,
  shuts it down, RE-spawns on the same repo, and asserts the old session id still
  resolves (`messages()` 200, vs 404 for a gone session). Confirms the assumption
  behind persisting `oc_session_id` — OpenCode's on-disk store survives a respawn
  — without the backend/UI/model. Run: `./.venv/bin/python scripts/verify_oc_reattach.py`
  (needs the `opencode` binary). Verified passing 2026-06-14.
- `verify_team_save.py` — browser regression for the **team-save data-loss
  class**: edits a team, switches the header team-selector to another team within
  the 600 ms autosave debounce window and back, then asserts via the API that the
  edit persisted (the bug the team-UX work both fixed and re-introduced). Needs a
  backend + Vite up; point at the stack via `AG_UI_URL` (defaults to :5181 — an
  isolated scripted stack, e.g. `scripted_backend.py 8011` + `npm run dev -- --port 5181`).
  Verified passing 2026-06-18. Fold future autosave/state-switch checks here or
  into `verify_ui.py` so there's one canonical UI gate.
