# Implementation Plan — Local Multi-Agent Software Team

This is the working task tracker for building the system described in
[`specs/overall-plan.md`](specs/overall-plan.md). Phases build on each other;
**a phase is not "done" until its tests pass and the work is committed.**

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked (see log.md)

Decisions and their rationale live in [`log.md`](log.md).

---

## Guiding rules for this build

- Build slowly, layer on layer. Do not skip ahead.
- Every phase ships its tests alongside the code. **Do not start the next task
  until the current tests pass.**
- Commit each logical chunk with a clear message.
- Models are **injected**, never constructed in place (enables FunctionModel tests).
- Everything is keyed by `team_id` / `session_id` from Phase 0 — the multi-repo
  spine is bought up front even though the UI hides it until later.
- Keep modules small (~300 line soft ceiling), logic pure where possible,
  side effects at the edges.

---

## Phase 0 — Skeleton + seams

Goal: both servers start from the terminal; SQLite has all four tables; the
`Session`/`SessionManager` wrappers exist and own (currently single)
registry/lock/gateway/bus; app auto-creates one team + one session on startup.
**This is where the multi-repo future is bought.**

- [x] 0.1 Backend project scaffold: `pyproject.toml`, venv, deps (fastapi,
      uvicorn, pydantic-ai, pytest, httpx, anyio), `backend/` package layout.
- [x] 0.2 `backend/db.py` — SQLite schema: `teams`, `sessions`, `agent_state`,
      `tasks`. Every row keyed by `team_id`/`session_id`. Migrations-on-startup
      (idempotent `CREATE TABLE IF NOT EXISTS`).
- [x] 0.3 `backend/models_domain.py` — Pydantic models for Team, Session,
      AgentSpec, Capabilities, Task (data shapes only; no behavior yet).
- [x] 0.4 `backend/sessions.py` — `Session` (owns repo_root, write_lock,
      gateway, event_bus, agent registry — all per-session, no globals) +
      `SessionManager` holding `dict[session_id, Session]`.
- [x] 0.5 `backend/teams.py` — `TeamStore` CRUD over the `teams` table.
- [x] 0.6 `backend/main.py` — FastAPI app, lifespan boots `SessionManager`,
      auto-creates one team + one session, `GET /health`, `GET /api/session`.
- [x] 0.7 Frontend scaffold: Vite + React + TS, dev proxy to backend,
      hello-world that fetches `/health`.
- [x] 0.8 `README.md` — how to run backend + frontend.
- [x] 0.9 Tests: `test_db.py` (tables exist, keyed columns present),
      `test_sessions.py` (manager creates/retrieves a session; nothing global).
      Also `test_main.py` (TestClient: health + session + team endpoints).
- [x] 0.10 All Phase 0 tests pass (13 passed) → **commit**.

## Phase 1 — Graph MVP

Goal: editable React Flow canvas; add nodes via floating "+"; drag-to-connect
edges; 5-tab sidebar shells; graph persists as the one team's definition.

- [x] 1.1 Backend: graph model in team definition (`graph.py` — pure
      structural + runnable validation); endpoints GET/PUT the team's graph.
- [x] 1.2 Frontend: installed `@xyflow/react` (v12.11); `Canvas.tsx` with
      React Flow + `graphMapping.ts` (backend ⇄ React Flow conversion).
- [x] 1.3 `AgentNode.tsx` custom node (name, model, entry-point ⭐, lifecycle
      badge placeholder).
- [x] 1.4 Floating "+" adds a node; drag-to-connect edges via handles.
- [x] 1.5 5-tab sidebar shells in `Sidebar.tsx` (Persona/Capabilities/Links/
      Agent/Stats) showing selected agent read-only.
- [x] 1.6 Persist graph to backend (debounced PUT) on change; load on mount.
- [x] 1.7 Tests: `test_graph.py` (structural + runnable validation; API
      round-trip incl. positions/labels; malformed graph → 422).
- [x] 1.8 All tests pass (21 backend) + frontend builds → **commit**.

## Phase 2 — Single working agent + tools

Goal: one Pydantic AI agent with an injected model; persona→instructions;
capability profile→generated dev toolset (sandboxed); `write_todos`; Agent tab
streams text/thinking/tools over SSE; Stats tab from LM Studio REST.
**Milestone: an agent edits the session's repo, uninterrupted, in its box, with a
visible checklist.**

- [x] 2.1 `tools.py` — dev toolset core: `read_file(range)` (emits an
      **edit-token** the model copies back), `write_file`, `edit_file` with
      content-hash staleness check, `list_dir`, `grep`, `run_bash`. Pure helpers
      (resolve, glob, effective_range, numbered_slice, hash, apply_line_edit).
- [x] 2.2 `capabilities.py` — `make_dev_toolset(DevTools)` builds toolset per
      profile (read-only never gets write/edit; no-bash never gets bash).
- [x] 2.3 `models.py` — per-agent model resolution (LM Studio/local OpenAI-
      compatible, openai:, infer fallback), **injected** into the agent.
- [x] 2.4 `persona.py` — sticky `instructions` builder (persona + tool/edit
      guidance + 3-task rule).
- [x] 2.5 `todos.py` — `write_todos` tool + `AgentDeps` + `all_completed`.
- [x] 2.6 `agents.py` — `build_agent(spec, model, dev_tools)`.
- [x] 2.7 `streaming.py` — SSE bridge (`format_sse`/`sse_stream`) + runner
      driven by `agent.iter()` (works with FunctionModel), publishing
      lifecycle/text/thinking/tool_call/tool_result/todos/done to the bus.
- [x] 2.8 Frontend: editable Persona + Capabilities tabs (fs level + advanced
      globs + bash + model select); Agent tab streams events + live todos + run
      box; lifecycle badges on canvas nodes via SSE.
- [x] 2.9 `stats.py` + Stats tab — LM Studio `/api/v0/models` rich stats +
      per-session usage tally.
- [x] 2.10 Tests: `test_tools.py`, `test_capabilities.py`, `test_agents.py`,
      `test_streaming.py` + env-gated `test_live_smoke.py`.
- [x] 2.11 All tests pass (46 + 1 skipped); **live-validated** against
      qwen2.5-coder-7b (created hello.txt in 17s) → **commit**.

## Phase 3 — Long-lived tasks (agents as background workers)

Goal: each agent runs as a background `asyncio` task with a lifecycle
(idle/running/waiting-on-agent/blocked/done) owned by the session registry; can
interject mid-run; status shown on canvas nodes.

- [x] 3.1 `runtime.py` — `RunningAgent`: long-lived `asyncio` task + inbox
      queue + lifecycle + history continuity + clean stop (cancel mid-run
      without marking blocked).
- [x] 3.2 Registry tracks RunningAgents (`attach/detach/running`); created on
      first run, stopped on shutdown.
- [x] 3.3 Endpoints: run / interject / stop; lifecycle events on the SSE bus.
- [x] 3.4 `agent_state.py` — `AgentStateStore` persists history (serialized via
      `ModelMessagesTypeAdapter`) + lifecycle + usage continuously after each
      run (resume rehydration deferred to Phase 9).
- [x] 3.5 Frontend: lifecycle badge on `AgentNode` (from SSE); Agent tab gains
      Interject + Stop (button morphs Run→Interject while busy).
- [x] 3.6 Tests: `test_runtime.py` (idle→running→idle; interjection continues
      with history; stop is clean & not blocked; state persisted+reloadable).
- [x] 3.7 All tests pass (50 + 1 skipped) + frontend builds → **commit**.

## Phase 4 — Agent-to-agent communication

Goal: `ask_agent(target, question)` delegation tool; dynamic neighbor-list
injection into instructions; Links tab edits edge labels; inter-agent messages
streamed/logged/visualized (edge animation).

- [x] 4.1 `a2a.py` — `ask_agent` tool + `Delegator` (shared `ctx.usage`,
      `UsageLimits`, must-be-neighbor + cycle + depth guards); session-scoped
      target resolution via injected `agent_factory`.
- [x] 4.2 Dynamic neighbor injection via `@agent.instructions` in `build_agent`
      (reads live graph each run).
- [x] 4.3 Inter-agent message log: new `messages` table + `MessageLog`;
      `a2a_message` SSE events; `GET /api/messages`.
- [x] 4.4 Frontend: `LinksTab` edits edge "why" labels (in/out lists); canvas
      animates the active edge on delegation (`useEvents.activeEdges`).
- [x] 4.5 Tests: `test_a2a.py` (neighbor list from edges; routing + answer;
      non-neighbor refused; cycle guard; full ask_agent tool flow with logging).
- [x] 4.6 All tests pass (56 + 1 skipped) + frontend builds → **commit**.

## Phase 5 — Task system (the headline milestone)

Goal: full `tasks` lifecycle (queued→running→blocked→needs_review→done/…);
`NewTaskDialog`; session-level `TaskBoard`; three completion signals
(self / reviewer agent / `check:` command); delegation tree via
`parent_task_id`; safety rails (turn/depth caps → blocked; cycle guard).
**"Give the team a task and track it to completion."**

- [x] 5.1 `tasks.py` — pure state machine (`ALLOWED_TRANSITIONS`,
      `validate_transition`, `parse_completion_signal`), `TaskStore` CRUD,
      `TaskRunner` orchestration with injected effect callables.
- [x] 5.2 Wired task execution to `RunningAgent.run_once` (awaitable single run
      sharing history + delegator).
- [x] 5.3 Completion gates: self_reported → done; `check:<cmd>` runs in repo
      (nonzero → needs_revision); `reviewer:<id>` runs a structured-output
      (`ReviewVerdict`) reviewer agent; revision ping-pong cap → blocked.
- [x] 5.4 Endpoints `POST/GET /api/tasks` + `GET /api/tasks/{id}`; `task_status`
      SSE events; `NewTaskDialog` intake (prompt + agent + signal).
- [x] 5.5 Frontend: `TaskBoard.tsx` Kanban (canvas ⇄ board toggle in header),
      sub-tasks nested under parent, refreshes on `task_status` events.
- [x] 5.6 Tests: `test_tasks.py` (transitions, signal parse, store round-trip,
      all three gates incl. check-fail→revision→recover, ping-pong→blocked,
      agent-error→blocked).
- [x] 5.7 `test_e2e_session.py` — FunctionModel end-to-end: task → todos →
      ask_agent(expert) → write_file → reviewer gate → done, asserting the REAL
      file, the logged delegation, and the final `done` status.
- [x] 5.8 All tests pass (66 + 1 skipped) + frontend builds → **commit**.

## Phase 6 — Compaction + LLM execution gateway

Goal: `history_processors` compaction (persona stays in instructions, never
touched); the gateway as the chokepoint ALL model calls route through, with the
per-session parallel/serial toggle; per-session write-lock confirmed correct.

- [ ] 6.1 `persona.py`/`history.py` — compaction history processor (summarize-
      oldest / slice-recent, keep tool-call pairs together; trigger near limit).
- [ ] 6.2 `gateway.py` — parallel pass-through | serial `Semaphore(1)`; route
      every model call (turns, ask_agent, reviewer, compaction) through it.
- [ ] 6.3 Per-session serial/parallel toggle + "waiting for model slot" SSE.
- [ ] 6.4 Tests: `test_gateway.py` (serial admits one at a time; parallel
      doesn't serialize; per-session isolation), `test_history.py` (compaction
      keeps instructions + tool-call pairs; triggers at threshold).
- [ ] 6.5 All tests pass → **commit**.

## Phase 7 — Team library

Goal: save current graph as a named team; list; load into editor. Pure CRUD on
`teams`; runtime already supports it. Enforce "session pins its definition at
launch."

- [ ] 7.1 Backend: team CRUD endpoints (already have store; add list/save/load).
- [ ] 7.2 Session pins (copies/versions) the team definition at launch.
- [ ] 7.3 Frontend: save-as / team list / load-into-editor UI.
- [ ] 7.4 Tests: `test_teams.py` (CRUD; editing template doesn't mutate a
      running session's pinned definition).
- [ ] 7.5 All tests pass → **commit**.

## Phase 8 — Multi-session

Goal: launch a team against a chosen repo (repo picker + same-repo warning);
session switcher; concurrent session views. Runtime already supports N sessions.

- [ ] 8.1 Backend: launch-session endpoint (team_id + repo_path + mode);
      same-repo active-session warning.
- [ ] 8.2 Frontend: `SessionSwitcher.tsx`, repo-picker dialog, per-session
      subscription.
- [ ] 8.3 Tests: `test_multisession.py` (two sessions isolated: separate locks/
      registries/buses; same-repo warning fires).
- [ ] 8.4 All tests pass → **commit**.

## Phase 9 — Snapshot/resume + polish (ongoing)

Goal: persist + rehydrate `agent_state` and in-flight `tasks` for pause/resume;
plus polish items as time allows.

- [ ] 9.1 Snapshot: persist agent histories + in-flight task state.
- [ ] 9.2 Resume: rehydrate a session from DB (full history + task state).
- [ ] 9.3 Tests: `test_resume.py` (snapshot → fresh manager → resume → state
      matches; in-flight task continues).
- [ ] 9.4 Polish backlog: cost estimates, persisted work logs, export/import
      teams, per-command bash allowlist, optional Docker sandbox executor.
- [ ] 9.5 Commit.

---

## Status summary

| Phase | Title | Status |
|---|---|---|
| 0 | Skeleton + seams | ✅ done (13 tests) |
| 1 | Graph MVP | ✅ done (21 tests + build) |
| 2 | Single working agent + tools | ✅ done (46 tests + live) |
| 3 | Long-lived tasks | ✅ done (50 tests) |
| 4 | Agent-to-agent | ✅ done (56 tests) |
| 5 | Task system | ✅ done (66 tests + e2e) |
| 6 | Compaction + gateway | not started |
| 7 | Team library | not started |
| 8 | Multi-session | not started |
| 9 | Snapshot/resume + polish | not started |
