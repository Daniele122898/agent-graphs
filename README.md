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
- **Pluggable model backends**: each agent picks a backend (local LM Studio or
  the hosted DeepSeek API) and a model from that backend's live list, plus —
  where the backend supports it — a thinking on/off toggle and a thinking
  effort level (DeepSeek: high/max). Adding another API is one small backend
  class.
- **Pluggable agent harness** (native | OpenCode): a session runs on either the
  built-in Pydantic AI engine or a headless [OpenCode](https://opencode.ai)
  server, chosen at launch — switchable side by side. Both expose the same
  operations (run, delegate via `ask_agent`, `ask_user`, history, todos,
  lifecycle, usage) and publish the same events, so the control room is
  identical regardless. OpenCode is pinned as a submodule (`vendor/opencode`,
  v1.16.2); the harness runs the installed `opencode` binary by default.
- **Capability-scoped tools**: read-only agents literally have no write
  tools; a hash-guarded line-range edit tool keeps weak local models from
  corrupting files.
- **Project context files, Claude Code-style**: when an agent reads a file,
  the `AGENTS.md`/`CLAUDE.md` files governing it (its directory and every
  directory up to the repo root) are injected into context once per
  conversation, each clearly delimited with the folder it applies to.
  `AGENTS.md` wins over `CLAUDE.md` when both exist in a directory.

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

### Configuration (API keys)

Hosted model backends read their credentials from `config.yml` at the repo
root, which is **gitignored** so keys are never committed. Copy the committed
example and fill in your own values:

```bash
cp config.example.yml config.yml
# then edit config.yml — e.g. set providers.deepseek.api_key
```

Environment variables override the file (`DEEPSEEK_API_KEY`,
`AGENT_GRAPHS_LMSTUDIO_URL`). Without a key the backend still runs; the
DeepSeek entry in the UI just shows "not configured" with a hint.

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
  main.py         app factory + lifespan (boot, rehydration)
  wiring.py       composition root: workers, task-runner effects, graph sync
  api/            HTTP/SSE endpoints, one module per resource + wire schemas
  domain/         pure Pydantic data shapes (the spine) + graph validation
  runtime/        Session/SessionManager, RunningAgent, gateway, bus,
                  streaming, task store + runner, usage tally
  agents/         building one agent: persona, capability-scoped tools,
                  todos, ask_agent delegation, ask_user, history compaction
  harness/        agent-execution abstraction: native (Pydantic AI) | opencode
  providers/      model backends (LM Studio, ...) + model-string resolution
  storage/        SQLite schema/connections, team + agent-state stores
frontend/       Vite + React + TypeScript control room
  src/lib/        api client, backend type mirrors, UI primitives
  src/hooks/      useEvents (SSE), useTeamGraph (React Flow state)
  src/canvas/     graph canvas: nodes, floating edges, mapping
  src/panels/     sidebar (+ tabs), task board, onboarding, session switcher
tests/          deterministic function + e2e tests (token-free)
scripts/        verify_ui.py (Playwright harness), scripted_backend.py
specs/          the design document + study notes (pi harness, LM Studio API)
plan.md         phased task tracker
log.md          decision log
```
