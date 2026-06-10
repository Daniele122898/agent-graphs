# Agent Graphs

A persistent, multi-agent software team that lives in a repo folder — long-lived
specialized workers that share one workspace, delegate to one another, and modify
a real codebase autonomously. The web app is a **control room** for watching and
steering that team.

See [`specs/overall-plan.md`](specs/overall-plan.md) for the full design and
[`plan.md`](plan.md) for the phased build status. Decisions are logged in
[`log.md`](log.md).

## Status

Built incrementally, phase by phase. Current progress is tracked in `plan.md`.

## What it does

- **Teams are templates, sessions are running instances** bound to a repo
  folder. Agents are long-lived workers with persistent conversations, not
  request handlers.
- **Tasks** with a status lifecycle and per-task completion gates
  (self-reported / reviewer agent / shell check), revision loops with hard
  caps, and one-click **Retry** for blocked tasks.
- **Delegation**: agents consult graph neighbors via `ask_agent` (cycle/depth
  guarded); delegated work runs on the target's real worker, fully visible.
- **ask_user**: an agent that needs a decision parks its run and asks the
  human — the control room renders the questions (multiple choice + free
  text) and the run resumes with the answers.
- **The Agent tab shows the real model context**: the system sections sent
  with every request plus the full stored conversation, with **Clear** and
  **Summarize** (model-written compaction) controls.
- **Capability-scoped tools**: read-only agents literally have no write
  tools; a hash-guarded line-range edit tool keeps weak local models from
  corrupting files.

## Requirements

- Python 3.12+ (developed on 3.13)
- Node 18+ (developed on 23)
- Optional: [LM Studio](https://lmstudio.ai/) or any OpenAI-compatible endpoint
  for local models (the test suite uses Pydantic AI's `FunctionModel` and needs
  no model server). **Local models must have LM Studio's `tool_use` capability**
  — models without it can't function-call, so agents can't do anything; the
  model picker in the UI marks usable models with 🛠.

## Setup

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Frontend

```bash
cd frontend
npm install
```

## Running

Two terminals.

**Backend** (FastAPI on :8000):

```bash
source .venv/bin/activate
# the graceful-shutdown cap matters: the SSE /events stream never finishes on
# its own, so without it --reload hangs at "Waiting for connections to close"
uvicorn backend.main:app --reload --port 8000 --timeout-graceful-shutdown 3
```

The backend starts empty — nothing is auto-created. Persisted sessions are
rehydrated on startup so your work survives restarts.

**Frontend** (Vite dev server on :5173, proxies `/api`, `/health`, `/events`):

```bash
cd frontend
npm run dev
```

Open http://localhost:5173. On first run you'll be guided to **create a team**
(an agent graph) and **launch a session** that binds it to a repo folder on
disk. The team then works in that folder.

## Tests

The suite is deterministic and token-free (Pydantic AI `FunctionModel` /
`TestModel`), so it runs with no model server:

```bash
source .venv/bin/activate
pytest
```

### Visual / browser verification

UI changes are verified by driving the real app with Playwright (screenshots in
`/tmp/ag_shots/`). One-time browser install, then run with both servers up:

```bash
playwright install chromium
python scripts/verify_ui.py
```

## Layout

```
backend/        FastAPI app + Pydantic AI agent harness
  main.py         app factory + lifespan + HTTP/SSE endpoints
  wiring.py       glue: workers, task-runner effects, graph sync
  db.py           SQLite schema: teams, sessions, agent_state, tasks, messages
  teams.py        TeamStore — team-definition CRUD (templates)
  sessions.py     SessionManager + Session (owns registry/lock/gateway/bus/questions)
  runtime.py      RunningAgent — the long-lived worker
  a2a.py          delegation (ask_agent) + inter-agent message log
  questions.py    ask_user — parks a run on the human's answer
  tasks.py        task store + runner (completion gates, revision caps)
  gateway.py      LLM execution gateway (parallel | serial)
  bus.py          per-session event bus
  models_domain.py  Pydantic data shapes (the spine)
tests/          deterministic function + e2e tests (token-free)
frontend/       Vite + React + TypeScript control room
scripts/        verify_ui.py (Playwright harness), scripted_backend.py
specs/          the design document + study notes (pi harness, LM Studio API)
plan.md         phased task tracker
log.md          decision log
```
