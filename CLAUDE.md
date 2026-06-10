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

## Architecture in one screen (the *why*)
- **Team = definition (template); Session = running instance bound to a repo.** This image-vs-container split is the spine of the data model and runtime — designed in from day one so multi-repo is UI work, not a rewrite. One process holds `dict[session_id, Session]`.
- **Nothing is a global singleton.** Each `Session` owns its own write-lock, LLM execution gateway, event bus, agent registry, usage tally. A global lock would wrongly serialize unrelated repos.
- **Agents are long-lived background workers**, not request handlers. The UI observes and interjects.
- **Tasks are first-class** with a status lifecycle and per-task completion gate (self-reported / reviewer agent / `check:` command). Code-level caps (turn/depth/revision) prevent runaway loops.
- **The LLM execution gateway is separate from the task system**: tasks = *what work exists*; gateway = *how model calls dispatch against compute* (serial on low-spec, else parallel). Don't conflate them.
- **Models are per-agent, provider-agnostic, and injected** (never constructed in place) so tests use a scripted fake model.

## Flow (IMPORTANT — changed from the early MVP)
- The backend does **NOT** auto-create a team or session. Startup only rehydrates persisted sessions. The user **explicitly** creates a team (gets a starter lead agent) and **launches a session** (team + repo) via the onboarding UI. Never reintroduce a hidden default team/session.

## Run & test
```bash
# backend (FastAPI :8000) — starts empty; create team + launch session in the UI
# (the graceful-shutdown cap matters: open SSE /events streams never finish, so
#  without it --reload wedges at "Waiting for connections to close" forever)
./.venv/bin/python -m uvicorn backend.main:app --reload --port 8000 --timeout-graceful-shutdown 3
# frontend (Vite :5173, proxies /api /health /events)
cd frontend && npm run dev
# backend tests — deterministic, token-free (FunctionModel); MUST be green
./.venv/bin/python -m pytest
# frontend — type-check + build (no unit-test runner)
cd frontend && npm run build
# visual UI verification (both servers up, fresh DB) — REQUIRED for UI changes
./.venv/bin/python scripts/verify_ui.py   # screenshots → /tmp/ag_shots/, then Read them
```
A real local model is available at `http://127.0.0.1:1234` (LM Studio, OpenAI-compatible). **Only models with LM Studio's `tool_use` capability work** — others emit tool calls as text that silently does nothing. Good choices: `qwen/qwen3.5-9b` (default) or `google/gemma-4-12b-qat`; `qwen2.5-coder-*` does NOT tool-call. Load/unload via the API (`specs/lmstudio-api.md`), ONE model at a time. **Use it sparingly** (weak laptop). The env-gated live tier is `tests/test_live_smoke.py` (`AGENT_GRAPHS_LIVE=1`).

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
