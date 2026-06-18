# CLAUDE.md — Agent Graphs

A persistent, multi-agent software team that lives in a repo folder: long-lived
specialized agents that share one workspace, delegate to one another, and modify
a real codebase. The web app is a **control room** to watch and steer them — it
is not the thing doing the work.

This is the root context. `backend/`, `frontend/`, and `tests/` each have their
own `CLAUDE.md` with subsystem detail (loaded when you open files there).

## Read these first
- `specs/overall-plan.md` — the full design and rationale (the source of truth for *intent*).
- `plan.md` — the phased build tracker (what's done, what's left). Tick items as you complete them.
- `log.md` — the **decision log**. See the maintenance rules below.

## Working agreements (YOU MUST)
- **Tests must pass before moving to the next task.** No exceptions.
- **Commit each logical chunk** with a clear message, locally on `main` (no remote configured; do not push unless asked). End commit messages with the Co-Authored-By trailer.
- **Maintain `log.md`**: when you make a non-obvious design decision, record it — *what, why, and reversibility*. This is how the user reviews and challenges choices later. Newest entries per phase.
- **Keep `CLAUDE.md` files current** (see the section at the bottom).
- **Verify UI changes in a real browser with Playwright** before claiming they work — see `frontend/CLAUDE.md`. Type-check/build alone is not sufficient for UI.
- The user (Daniele) is often away; **work autonomously and only stop if genuinely blocked.** Don't delete local state you didn't create (e.g. `backend/db.sqlite`) without checking — it holds the user's experimentation.
- **API keys live ONLY in the gitignored `config.yml`** (loaded by `backend/config.py`; `config.example.yml` is the committed shape). NEVER commit a key — grep the staged diff for `sk-` before any commit touching config or docs.

## Architecture in one screen (the *why*)
- **Team = definition (template); Session = running instance bound to a repo.** This image-vs-container split is the spine of the data model and runtime — designed in from day one so multi-repo is UI work, not a rewrite. One process holds `dict[session_id, Session]`.
- **Sessions can be rebound to a different team while idle** via `POST /api/sessions/{id}/rebind` → `SessionManager.rebind()` (async; 409 if any agent is busy). Agents common to both teams keep their history; new ones register; removed ones are detached and their persisted state dropped; a **repurposed slot** (same id, different role *name*) has its history reset — identity for history carry-forward is `(id, name)`, not id alone (see `backend/runtime/CLAUDE.md`). The graph editor is team-scoped (not session-scoped): you can edit any team's graph independently of the running session — edits to a non-session team don't affect live sessions; "↻ Use for session" rebinds explicitly; "Save as new team…" is a pure fork (copy + switch the editor, never silently rebind the session). The frontend autosave **flushes the outgoing team on every team switch** (the hook owns it), so switching the editor can't drop unsaved edits (see `frontend/CLAUDE.md`).
- **Nothing is a global singleton.** Each `Session` owns its own write-lock, LLM execution gateway, event bus, agent registry, usage tally. A global lock would wrongly serialize unrelated repos.
- **Agents are long-lived background workers**, not request handlers. The UI observes and interjects.
- **Tasks are first-class** with a status lifecycle and per-task completion gate (self-reported / reviewer agent / `check:` command). Code-level caps (turn/depth/revision) plus a per-task **timeout in hours** (default 1h, 0 = no limit) prevent runaway loops. On opencode, a delegating agent's task is "done" only when its **whole delegation subtree** is (subtree-aware quiescence — see `backend/harness/CLAUDE.md`), never on a premature first turn.
- **The LLM execution gateway is separate from the task system**: tasks = *what work exists*; gateway = *how model calls dispatch against compute* (serial on low-spec, else parallel). Don't conflate them.
- **Models are per-agent, provider-agnostic, and injected** (never constructed in place) so tests use a scripted fake model.
- **The agent harness is pluggable per session** (`backend/harness/`): `native` (our Pydantic AI engine) or `opencode` (a headless OpenCode server, pinned at `vendor/opencode`). Every agent operation routes through `session.harness`; both publish the SAME bus events + lifecycle, so the product (tasks, delegation, ask_user, control room) is harness-agnostic. See `backend/harness/CLAUDE.md`.
- **Agents must never stall silently**: an agent needing the human calls the `ask_user` tool (run parks on `waiting-on-user`, the UI renders an answer card, the run resumes with the answers); a task run that ends with open todos gets a capped continuation nudge.

## Flow (IMPORTANT — changed from the early MVP)
- The backend does **NOT** auto-create a team or session. Startup only rehydrates persisted sessions. The user **explicitly** creates a team (gets a starter lead agent) and **launches a session** (team + repo) via the onboarding UI. Never reintroduce a hidden default team/session.

## Run & test
```bash
# backend (FastAPI :8000) — starts empty; create team + launch session in the UI.
# ALWAYS run via `python -m backend` (NOT raw `uvicorn backend.main:app`): the
# entrypoint bakes in timeout_graceful_shutdown, without which an open SSE
# /events stream wedges shutdown at "Waiting for connections to close" forever
# (backend/__main__.py + runtime/bus.py explain why). --reload for dev.
./.venv/bin/python -m backend --reload
# frontend (Vite :5173, proxies /api /health /events)
cd frontend && npm run dev
# SINGLE-PROCESS (self-host) mode — for dogfooding the tool ON THIS REPO: build
# the UI, then run the backend WITHOUT --reload; it serves the built frontend
# from :8000 too (StaticFiles, added last so /api,/health,/events still win). No
# Vite HMR + no --reload means an agent editing this repo can't hot-swap code
# mid-run and kill the live session. Rebuild + restart for UI changes
# (intentional). Skipped automatically if frontend/dist is absent.
cd frontend && npm run build && cd .. && ./.venv/bin/python -m backend   # open :8000
# backend tests — deterministic, token-free (FunctionModel); MUST be green
./.venv/bin/python -m pytest
# frontend — type-check + build (no unit-test runner)
cd frontend && npm run build
# visual UI verification (both servers up, fresh DB) — REQUIRED for UI changes
./.venv/bin/python scripts/verify_ui.py   # screenshots → /tmp/ag_shots/, then Read them
```
A real local model is available at `http://127.0.0.1:1234` (LM Studio, OpenAI-compatible). **Only models with LM Studio's `tool_use` capability work** — others emit tool calls as text that silently does nothing. The capability flag isn't sufficient either: `unsloth/gemma-4-12b-it-qat` claims tool_use but its jinja template crashes on any request with tools ("Cannot call something that is not a function") — probe a new model with a tiny tools request before trusting it. Good choice: `qwen/qwen3.5-9b` (default); `qwen2.5-coder-*` does NOT tool-call; 12B+ models tend to exhaust this laptop (KV-cache quantization + big contexts cause "Channel Error" worker crashes, especially on gemma). Load/unload via the API (`specs/lmstudio-api.md`), ONE model at a time. **Use it sparingly** (weak laptop). The env-gated live tier is `tests/test_live_smoke.py` (`AGENT_GRAPHS_LIVE=1`).

## Definition of done (YOU MUST satisfy before claiming a task complete)
A clean `tsc`/build is NECESSARY, NEVER SUFFICIENT — it can't see a dropped
autosave, an unguarded endpoint, or a broken flow. The team-switch data-loss bug
shipped green precisely because no *behavioral* gate ran. Tick every line that applies:
- **Backend tests green**: `./.venv/bin/python -m pytest`.
- **Frontend builds**: `cd frontend && npm run build`.
- **UI *behavior* changed** (persistence, switching, lifecycle)? A browser
  regression MUST exercise it and you MUST run it + Read the screenshots — extend
  `scripts/verify_ui.py` (or run `scripts/verify_team_save.py`: edit, switch
  within the 600 ms debounce, switch back, assert via the API the edit survived).
  Type-check is not UI verification.
- **New/changed mutating endpoint** (POST/PUT/DELETE touching session graph,
  agent history, or the DB)? It MUST go through a component/`SessionManager`
  method (never `app.state.*._conn`); be `async` if it swaps `session.graph`;
  carry a busy-guard (`wiring.require_session_idle`/`require_agent_idle`) if a
  live run depends on what it mutates; and be classified in
  `tests/test_endpoint_contracts.py` (the route-contract backstop).
- **Diverged from your plan/spec?** Do the planned item or record in `log.md`
  *why* it was dropped — never silently skip it (esp. the browser verification).
- **Touched `config.yml`/docs?** `git diff --staged | grep sk-` is empty.

## What's left to do
The 10 build phases are complete (see `plan.md`). The remaining backlog (Phase 9.4, ongoing): cost estimates, per-command bash allowlist, optional Docker sandbox executor. (The persisted work-log UI shipped 2026-06-10: the Agent tab renders the stored history + system context, with Clear/Summarize.) The path-check sandbox is **not** escape-proof (accepted for v1, local single-user).

## Keeping CLAUDE.md files current
After any change to the codebase, update the relevant CLAUDE.md (this file, a
subdirectory's CLAUDE.md, or both) if the change affects architecture, intent,
constraints, or non-obvious design decisions.

CLAUDE.md files should capture what the code cannot explain itself: *why* a
design was chosen, tradeoffs that were consciously made, constraints from
outside the codebase, and the intended direction of incomplete work. Do not
duplicate what is obvious from reading the source.

When a mistake is corrected more than once in conversation, record the correct
behavior in the most specific CLAUDE.md that applies (subdirectory > parent >
root) so the guidance is available in future sessions without re-teaching it.
