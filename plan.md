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

- [x] 6.1 `history.py` — `compact_history` (pure slice-recent; cuts only at a
      clean user-prompt boundary so tool-call/return pairs never orphan) +
      `compaction_capability()`; wired into every agent via `ProcessHistory`.
- [x] 6.2 `gateway.py` — `Gateway.slot()` (serial `Semaphore(1)` | parallel
      no-op) + `GatedModel(WrapperModel)` so every model call (turns, ask_agent,
      reviewer, compaction) routes through the session gateway.
- [x] 6.3 Per-session mode toggle `POST /api/session/mode` + header control;
      `model_wait` SSE event via the gateway's `on_wait` callback.
- [x] 6.4 Tests: `test_gateway.py` (serial non-interleave, parallel interleave,
      on_wait fires, per-session isolation, GatedModel transparent),
      `test_history.py` (no-op below threshold, user-anchored window, no orphaned
      tool returns).
- [x] 6.5 All tests pass (75 + 1 skipped) + frontend builds → **commit**.

## Phase 7 — Team library

Goal: save current graph as a named team; list; load into editor. Pure CRUD on
`teams`; runtime already supports it. Enforce "session pins its definition at
launch."

- [x] 7.1 Backend: team CRUD endpoints — `GET/POST /api/teams`,
      `GET /api/teams/{id}`, `GET/PUT /api/teams/{id}/graph`, rename. Shared
      `_apply_team_graph` helper validates + persists.
- [x] 7.2 Session pins its definition at launch (graph copied at create);
      `_apply_team_graph` syncs *only* the session bound to that team.
- [x] 7.3 Frontend: header team selector (load-into-editor) + "Save as…";
      graph hook is team-scoped (`useTeamGraph(teamId)` + `snapshot()`).
- [x] 7.4 Tests: `test_teams.py` (CRUD; editing a *template* doesn't mutate a
      running session; editing the session's own team does sync; malformed → 422).
- [x] 7.5 All tests pass (79 + 1 skipped) + frontend builds → **commit**.

## Phase 8 — Multi-session

Goal: launch a team against a chosen repo (repo picker + same-repo warning);
session switcher; concurrent session views. Runtime already supports N sessions.

- [x] 8.1 Backend: `POST /api/sessions` (launch team_id+repo_path+mode, returns
      same-repo warning), `GET /api/sessions`; `session_id` query param threaded
      through all session-scoped endpoints (defaulting to the auto-created one).
- [x] 8.2 Frontend: `SessionSwitcher.tsx` (switch + launch form); active
      session id is module state in `api.ts` so calls target it; `useEvents`
      reconnects per session.
- [x] 8.3 Tests: `test_multisession.py` (launch + list; independent
      bus/lock/gateway; same-repo warning fires; `?session_id=` targets the
      right session's tasks).
- [x] 8.4 All tests pass (82 + 1 skipped) + frontend builds → **commit**.

## Phase 9 — Snapshot/resume + polish (ongoing)

Goal: persist + rehydrate `agent_state` and in-flight `tasks` for pause/resume;
plus polish items as time allows.

- [x] 9.1 Snapshot: agent histories persisted continuously (Phase 3) + in-flight
      tasks persisted in the `tasks` table (Phase 5) — the snapshot already
      exists; this phase adds the rehydrate side.
- [x] 9.2 Resume: `SessionManager.resume_session(id, graph)` rehydrates a
      persisted session into memory; `RunningAgent(initial_messages=...)` seeds
      from persisted history; `POST /api/sessions/{id}/resume` endpoint.
- [x] 9.3 Tests: `test_resume.py` (fresh manager rehydrates session + history,
      agent continues with context; in-flight tasks survive across managers).
- [~] 9.4 Polish backlog (ongoing): export/import teams = the team CRUD
      (`GET/POST /api/teams` with graph); cost estimates, persisted work-log UI,
      per-command bash allowlist, Docker sandbox executor — **not yet built**,
      left as the explicit ongoing backlog.
- [x] 9.5 All tests pass (84 + 1 skipped) → **commit**.

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
| 6 | Compaction + gateway | ✅ done (75 tests) |
| 7 | Team library | ✅ done (79 tests) |
| 8 | Multi-session | ✅ done (82 tests) |
| 9 | Snapshot/resume + polish | ✅ core done (84 tests); polish backlog ongoing |
