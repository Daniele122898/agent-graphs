# Decision Log

A running record of decisions taken during the build and *why*, so they can be
reviewed and challenged later. Newest entries at the bottom of each phase.
Format: `YYYY-MM-DD — decision — rationale — reversibility`.

---

## Phase 0 — Skeleton + seams

### 2026-06-09 — Session kickoff decisions (from the user, before starting)

- **Checkpointing: run straight through all phases, stop only if blocked.**
  User is away; wants maximum progress with a reviewable trail (this log +
  per-chunk commits + plan.md ticks). Reversible: they can challenge any commit.
- **Live-model testing: FunctionModel/TestModel is primary; the local model at
  `http://127.0.0.1:1234` is to be used SPARINGLY.** User's laptop and the local
  model are weak. So the automated suite is fully deterministic and token-free;
  real-model validation is a thin, env-gated, off-by-default tier I only run by
  hand when genuinely needed. Live endpoint is OpenAI-compatible; best
  tool-caller available is `qwen2.5-coder-7b-instruct-mlx`.
- **Git: trunk renamed `master`→`main`; commit each logical chunk locally; no
  remote/push** (none configured). Matches the repo's configured main branch.

### 2026-06-09 — Toolchain choices

- **Python 3.13 + stdlib `venv` + `pip` + `pyproject.toml`** rather than
  `uv`/`poetry`. Reason: `uv`/`poetry` are not installed and the user is away —
  installing global tooling unprompted is intrusive. `venv`+`pip` is already
  present and sufficient for a local single-user project. Reversible: can adopt
  `uv` later without changing the package layout.
- **Project layout: `backend/` and `frontend/` at repo root** (not a nested
  `agentteam/`). Reason: this repo *is* the project; an extra nesting level adds
  nothing. The spec's `agentteam/` was illustrative. Module names follow the
  spec's suggested structure otherwise.
- **SQLite via the stdlib `sqlite3` module**, file at `backend/db.sqlite`
  (gitignored). Zero extra deps, matches spec. Schema applied idempotently on
  startup (`CREATE TABLE IF NOT EXISTS`) — no migration framework yet.
- **`pydantic-ai-slim[openai]` (resolved to 1.106.0), not full `pydantic-ai`.**
  Slim core includes `FunctionModel`/`TestModel` (our test seam) and the OpenAI
  provider covers LM Studio's OpenAI-compatible endpoint. Avoids pulling every
  provider SDK we don't use. Reversible: add extras as providers are needed.
- **Module named `models_domain.py`, not `models.py`** (spec's name), to avoid
  collision with the Phase 2 `models.py` (per-agent *model* resolution). Domain
  data shapes vs. LLM-model wiring are different concerns; keeping the names
  distinct prevents confusion.
- **`create_app(db_path, repo_path)` factory** so tests inject a temp DB + temp
  repo and exercise the real lifespan via FastAPI `TestClient`. The clock is
  injected into `TeamStore`/`SessionManager` (`fake_clock` fixture) for
  deterministic timestamps. This is the dependency-injection discipline the spec
  requires for `FunctionModel` tests later.
- **Default session repo = gitignored `workspace/`** (overridable via
  `AGENT_GRAPHS_REPO`). Gives agents a real folder to edit out of the box
  without pointing at anything important. The auto-created team has a single
  entry-point "lead" agent so a team always satisfies the ">=1 entry point" rule.

## Phase 1 — Graph MVP

### 2026-06-09 — Graph editor decisions

- **Two-tier graph validation, separated on purpose** (`graph.py`):
  *structural* (unique ids, edges reference real nodes, no self-loops/dupes) is
  enforced on every save (PUT → 422 on failure); *runnable* (>=1 entry point,
  non-empty) is only required at session launch. Reason: the editor must let you
  build a graph incrementally — forcing an entry point before you've added one
  would make editing miserable. The save endpoint rejects only what would
  corrupt the runtime.
- **React Flow node `data` carries the full `AgentSpec`; node `position` is the
  only UI-owned field.** `graphMapping.ts` is the single place the two formats
  meet, so the rest of the UI never juggles both. The backend round-trip test
  guards the wire format; the TS types guard the frontend.
- **Persistence is a debounced (600ms) PUT of the whole graph** on any change,
  guarded by a `loaded` ref so the initial load doesn't echo back a save.
  Whole-graph PUT (not per-node patches) is simplest and the graph is tiny;
  revisit only if graphs get large.
- **Sidebar is one `Sidebar.tsx` with inline tab shells**, not five files yet.
  The spec lists `Sidebar/{...}.tsx`; splitting is premature while they're
  read-only shells. Will split when each gains real editing behavior (Phase 2+).
- **Browser-level verification deferred.** Phase 1 confidence rests on backend
  graph tests (round-trip + validation, 21 passing) + a clean frontend
  type-check/build. No headless browser test yet; the canvas interactions
  (add node, drag-connect) are standard React Flow and will be exercised
  for real once an agent is wired up (Phase 2).

## Phase 9 — Snapshot/resume + polish

### 2026-06-09 — Resume is the rehydrate half; snapshot already existed

- **The snapshot side was already built** — `agent_state` is written
  continuously from Phase 3, and `tasks` are persisted from Phase 5. So Phase 9
  is just the *rehydrate* logic: `SessionManager.resume_session(id, graph)`
  reconstructs a live `Session` from its DB row, and
  `RunningAgent(initial_messages=...)` seeds from the persisted history so a
  resumed agent continues with full context (verified by `test_resume.py` with
  a fresh manager on the same DB, simulating a new process).
- **Resume UI is intentionally minimal** (an endpoint, no dedicated button).
  The spec scoped "resume-and-rehydrate logic + UI" to Phase 9; the logic +
  endpoint are the load-bearing part. A one-click resume button is trivial to
  add on top.
- **Polish backlog (9.4) is explicitly ongoing, not silently skipped.**
  export/import teams already falls out of the team CRUD. Cost estimates,
  persisted work-log UI, per-command bash allowlist, and the optional Docker
  sandbox executor are documented as not-yet-built in plan.md.

## Phase 8 — Multi-session

### 2026-06-09 — Threading session_id

- **`_default_session(app)` became `_session(app, session_id=None)`** —
  resolves a given id or falls back to the auto-created default. Every
  session-scoped endpoint gained an optional `session_id` query param, so the
  MVP UI keeps working without one while the API is fully multi-session. The
  runtime already held `dict[session_id, Session]` from Phase 0, so this was
  plumbing, not architecture — exactly the payoff the spec predicted.
- **Same-repo launch warns but doesn't block** (`active_sessions_for_repo`),
  per spec — two task forces over one repo is allowed but flagged.
- **Frontend active session is module state in `api.ts`** (`setActiveSession`),
  so session-scoped fetches target it without threading a prop through every
  component. `useEvents(sessionId)` reconnects (and clears accumulated state) on
  switch. Pragmatic for a single-user local UI; a multi-tab future would move
  this into context/props.

## Phase 7 — Team library

### 2026-06-09 — Pin semantics made explicit

- **`_apply_team_graph(team_id, graph)` is the one place graph edits land**, used
  by both the default `/api/team/graph` and the new `/api/teams/{id}/graph`. It
  syncs the running session's in-memory graph **only if** that session is bound
  to the edited team. Editing any *other* saved team (a library template) never
  touches a running session — that's the pin-at-launch guarantee, now
  test-enforced (`test_editing_a_template_does_not_mutate_a_running_session`).
- **The editor became team-scoped on the frontend** (`useTeamGraph(teamId)` +
  `snapshot()`), with a header team selector (load-into-editor) and "Save as…".
  The session's own team is marked "(session)" so it's clear which one is live.
  This is the whole price of the team library — the runtime already supported
  multiple team definitions; only the CRUD endpoints + a selector were missing.

## Phase 6 — Compaction + LLM execution gateway

### 2026-06-09 — Decisions

- **Compaction is slice-recent, not summarize-oldest (v1).** Deterministic and
  needs no extra model call. The only hard requirement is correctness: the kept
  window must begin at a clean boundary (a `ModelRequest` with a real
  `UserPromptPart`), never mid tool-call/return pair — else the model sees an
  orphaned tool result. If no clean boundary exists in the tail, leave history
  intact. Persona is untouched because it lives in `instructions`, not history.
- **History processing is a pydantic-ai *capability* (`ProcessHistory`) in
  1.x**, not an `Agent(history_processors=...)` kwarg (that param doesn't exist
  in 1.106). Added to every agent via `compaction_capability()`.
- **The gateway serializes at the *model-call* level via `GatedModel`
  (a `WrapperModel`), not by wrapping whole agent runs.** Wrapping whole runs
  with a `Semaphore(1)` would deadlock on delegation (the parent run holds the
  slot while `ask_agent` waits for it). Gating each `request`/`request_stream`
  means turns, delegations, reviewer gates, and compaction all serialize
  correctly and independently. `GatedModel` is transparent in parallel mode.
- **`Gateway.slot()` async CM is the primitive**; `run()` is a thin wrapper.
  `on_wait` fires when a serial slot is busy so the UI can show "waiting for
  model slot" (`model_wait` SSE). Mode is per-session (toggle endpoint), proving
  the gateway is not a global — a low-spec local session can serialize while a
  hosted session runs parallel.
- **Note:** `agent.iter()` uses `model.request` (non-stream) per node, so
  `GatedModel.request` is the hot path; `request_stream` gating is there for
  completeness/real streaming. This is why wrapping FunctionModel works fine.

## Phase 5 — Task system (headline milestone)

### 2026-06-09 — Task orchestration design

- **`TaskRunner` takes its effectful steps as injected callables**
  (`run_agent`, `run_reviewer`, `run_check`). This is the key testability move:
  the whole completion-gate + revision-loop + blocked-on-cap orchestration is
  tested with plain fakes (no models, no subprocess) in `test_tasks.py`, while
  `main.py` wires the real callables (RunningAgent / reviewer Agent / shell).
- **Task execution uses `RunningAgent.run_once` (awaitable), not the `submit`
  inbox.** The task system needs the result back to apply a gate, so it runs the
  assigned agent to completion and returns output. `run_once` shares the same
  agent/history/delegator as the interactive path — delegation and continuity
  behave identically. The inbox (`submit`/`start`) remains the interactive
  Agent-tab path; tasks never start the loop. (Caveat: running a task and
  interjecting on the same agent simultaneously is user error; not guarded.)
- **Reviewer gate uses structured output** (`output_type=ReviewVerdict`,
  `{approved, critique}`) — the evaluator-optimizer pattern. On reject, the
  critique is injected into the next run's prompt (needs_revision→running).
  A hard `MAX_REVISION_ROUNDS=3` cap stops reviewer/check ping-pong by parking
  the task in `blocked` (the user's attention) rather than looping forever.
- **Turn caps:** delegation depth is capped in a2a (Phase 4); a runaway worker
  run surfaces as an exception from the agent run, which `TaskRunner` catches →
  `blocked` with the error in the result. No infinite loops by construction.
- **The e2e spine test is the proof of the whole system.** `test_e2e_session.py`
  drives task→todos→ask_agent→write_file→reviewer→done with FunctionModel and
  asserts the REAL file content, the logged delegation, and the final status —
  zero tokens. Green here means sandbox + toolset + delegation + gates + state
  machine are all wired correctly together.

## Phase 4 — Agent-to-agent communication

### 2026-06-09 — Delegation design

- **`ask_agent(target, question)` runs a *fresh* sub-run of the target, not an
  interjection into the target's long-lived inbox.** Delegation should answer
  from the target's expertise without polluting the asker's context or
  disturbing the target's own task queue — so the `Delegator` builds a throwaway
  agent run for the question and returns its output. Shared `ctx.usage` +
  `UsageLimits(request_limit=50)` keep delegated work on the same budget.
- **Three structural guards in code (not prompt):** target must be a graph
  neighbor of the asker; no revisiting an agent already in the delegation chain
  (cycle guard); chain length capped at `MAX_DELEGATION_DEPTH=3`. All raise
  `ModelRetry` so the model self-corrects rather than the run crashing. This is
  the inoculation against A→B→C→A runaway loops.
- **`agent_factory` is injected into `Delegator`** so tests fully control each
  target's model (FunctionModel) and the production path resolves real models +
  DevTools. RunningAgent supplies a factory that builds targets with their own
  capabilities + the session write-lock + an injected `model_resolver`.
- **Neighbor list is injected dynamically** via `@agent.instructions` reading
  the live graph each run, so editing edges immediately changes who an agent
  knows to consult — no rebuild needed.
- **Added a 5th table, `messages`**, for the inter-agent log. The spec's
  "four-table spine" is about the *core* entities (teams/sessions/agent_state/
  tasks); the message log is a peripheral observability store the spec explicitly
  calls for ("stream/log inter-agent conversations"). Kept separate from
  `agent_state` (which is per-agent history) because it's a cross-agent stream.

## Phase 3 — Long-lived tasks

### 2026-06-09 — RunningAgent and interjection semantics

- **An agent is a durable `asyncio` worker with an inbox queue, not a
  request handler.** `RunningAgent` (`backend/runtime.py`) loops: pull a prompt,
  run it (history threaded so follow-ups build on context), persist, return to
  idle if nothing queued.
- **Interjection is *queued*, not spliced mid-turn — a deliberate, honest
  choice.** Pydantic AI runs a whole multi-turn `iter` to completion; you can't
  inject a user message between its internal model turns. So an interjection
  submitted while running is processed the instant the current run finishes,
  with full history — which reads as continuous work. The agent stays visibly
  "running" while items are queued. To *truly* interrupt, `stop()` cancels the
  in-flight run. Documented in the module + the UI button morphs Run→Interject.
- **`stop()` cancels cleanly without marking blocked.** `CancelledError` is
  re-raised past the `except Exception` handler (it's `BaseException` in 3.13),
  so a deliberate stop never looks like a failure; `stop()` then sets `idle`.
  Tested by `test_stop_is_clean_and_does_not_mark_blocked`.
- **`agent_state` is written continuously from Phase 3** (per spec), serialized
  via `ModelMessagesTypeAdapter` so a stored history round-trips back into
  `message_history`. Only the resume *rehydration* UI is deferred to Phase 9.
- **RunningAgent caches its resolved model.** Editing an agent's model in the
  Capabilities tab won't affect a running worker until it's stopped (then the
  next run recreates it with the new model). Acceptable for the MVP; revisit if
  hot model-swap is wanted.

## Phase 2 — Single working agent + tools

### 2026-06-09 — The edit tool: line-range + content-hash + edit-token

- **The edit tool ships as line-range + content-hash (per spec v1), but with a
  model-usability twist: `read_file` appends an `[edit-token <start>-<end>
  <hash>]` the model copies verbatim into `edit_file`.** Reason: a weak local
  model cannot compute a sha1 of the target lines, so requiring a raw hash arg
  would be unusable. The token is a coarse, range-level stand-in for oh-my-pi's
  per-line hashline anchors — it keeps both wins (no re-emitted surrounding
  code; stale-edit rejection when the file changed since the read) while being
  copy-paste trivial for the model. Graduate to full per-line anchors only if
  edit reliability disappoints. `effective_range` is the single source of truth
  shared by `numbered_slice` and the token hash so they never disagree.

### 2026-06-09 — Streaming via agent.iter, not run_stream_events

- **The streaming runner uses `agent.iter()` (node-level), not
  `run_stream_events()` (token deltas).** Reason: `run_stream_events` requires
  the model to support streaming, and `FunctionModel` (our deterministic test
  seam) does not unless given a `stream_function`. `agent.iter()` works with
  plain `FunctionModel`, so the runner is fully exercised in tests with zero
  tokens. Node-level granularity (tool calls/results via `CallToolsNode.stream`,
  text/thinking from response parts) is exactly what the Agent tab needs.
  Token-by-token deltas for real models are an additive enhancement, not a
  prerequisite — deferred.
- **Tools are plain (no `RunContext`); events come from the stream, not from
  inside tools.** Keeps the sandbox logic pure and testable. Only `write_todos`
  takes `ctx` (it mutates `AgentDeps.todos`).

### 2026-06-09 — Other Phase 2 choices

- **`models_domain` name kept; new `models.py` is model *resolution*.** A model
  string (`lmstudio:qwen…`, `openai:gpt-4o`, else `infer_model`) → instance,
  **injected** into the agent. Tests inject `FunctionModel` instead.
- **Run endpoint is fire-and-forget `asyncio.create_task`** stored in
  `app.state.running_tasks`. This is a Phase-2 precursor to the Phase-3
  `RunningAgent`; lifecycle is already published over SSE and reflected on
  canvas nodes. Errors land the agent in `blocked` + an `agent_error` event,
  never a silent failure.
- **PUT /api/team/graph syncs the running session's graph** (single-session
  MVP). The pin-at-launch vs. live-edit distinction is real but only matters
  once there are multiple sessions (Phase 7/8); deferred there.
- **Stats from LM Studio `/api/v0/models`** (richer than `/v1/models`):
  quantization, state, capabilities (tool_use), and the loaded-vs-max context
  quirk flagged in the UI. Endpoint returns a friendly payload (not 500) when
  LM Studio is down, so the UI degrades gracefully.
- **Live validation done once (sparingly, per the user):** real
  qwen2.5-coder-7b created `hello.txt` with content `hi` in 17s, streaming
  tool_call/tool_result/todos events. Confirms the whole stack works with an
  actual LLM. Repeatable via `AGENT_GRAPHS_LIVE=1 pytest tests/test_live_smoke.py`.

### 2026-06-09 — Phase 0 complete

- 13 tests pass; real uvicorn server verified (`/health`, `/api/session`,
  `/api/team`); frontend builds (`tsc -b && vite build`). The per-session
  ownership of lock/gateway/bus/registry is asserted by
  `test_infrastructure_is_per_session_not_global` — the "nothing is a global
  singleton" invariant is now test-enforced, not just intended.
