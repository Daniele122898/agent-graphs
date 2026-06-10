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

## Requirements

- Python 3.12+ (developed on 3.13)
- Node 18+ (developed on 23)
- Optional: [LM Studio](https://lmstudio.ai/) or any OpenAI-compatible endpoint
  for local models (the test suite uses Pydantic AI's `FunctionModel` and needs
  no model server).

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
uvicorn backend.main:app --reload --port 8000
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
  db.py           SQLite schema: teams, sessions, agent_state, tasks
  teams.py        TeamStore — team-definition CRUD (templates)
  sessions.py     SessionManager + Session (owns registry/lock/gateway/bus)
  gateway.py      LLM execution gateway (parallel | serial)
  bus.py          per-session event bus
  models_domain.py  Pydantic data shapes (the spine)
  main.py         app factory + lifespan
tests/          deterministic function + e2e tests
frontend/       Vite + React + TypeScript control room
specs/          the design document
plan.md         phased task tracker
log.md          decision log
```
