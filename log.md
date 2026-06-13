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

## Post-MVP — proper team/session flow + UI redesign (user-driven)

### 2026-06-10 — No more auto-create; explicit flow

- **Removed the "auto-create one hidden default team+session on startup" MVP
  behavior** (user: "i dont want it to constantly create a default team").
  Startup now only *rehydrates* persisted sessions; teams and sessions are
  created explicitly. The frontend shows an **Onboarding** card when there are
  no sessions: create a team (gets a starter lead agent) → launch a session
  (team + repo + mode). This also fixes the DB pollution where every restart
  piled up another "Default Team" (the user had seen 4).
- **`create_app` no longer takes `repo_path`** and there is no implicit default
  session — `_session()` requires a `session_id`. Removed `/api/team` and
  `/api/team/graph` (default-team endpoints); the team-scoped
  `/api/teams/{id}/graph` is the only graph editor path. Frontend persists the
  active session id in `localStorage` and reconciles it against the live list.

### 2026-06-10 — Model-switch bug fixed (cached worker)

- **Changing an agent's model (or persona/caps) now takes effect on the next
  run.** Root cause: `_get_or_create_running` cached the `RunningAgent`, whose
  pydantic Agent was built with the model resolved at creation — so a later edit
  was ignored (user hit a stale `gemma` error after switching). Fix:
  `RunningAgent` snapshots its built spec (`spec_changed()`), and
  `_get_or_create_running` rebuilds the worker (carrying history forward) when
  the spec changed. Regression test: `test_model_switch.py`.

### 2026-06-10 — UI redesign (design system)

- **Introduced a coherent design system** (`src/index.css` tokens + `src/ui.tsx`
  primitives: Button/IconButton/Select/TextInput/TextArea/Field/Chip) and
  restyled every surface. Light theme, blue (#2563eb) accent, role-based tokens,
  CSS-variable driven, WCAG-aware contrast — grounded in current palette
  guidance (IxDF / Figma / WebOsmotic). No more raw HTML buttons; consistent
  focus rings, radii, shadows. Agent transcript is now chat bubbles (user right
  blue, agent left) and echoes the user's own prompt (a `user_message` SSE
  event). The "+" add-agent control is a bottom-left FAB.

### 2026-06-10 — Browser-driven verification

- **Playwright is now the visual-verification path** (`scripts/verify_ui.py`):
  launches chromium, drives the real app (onboarding → create team → launch →
  control room → agent chat → task board), asserts structure, and screenshots to
  `/tmp/ag_shots/`. The screenshots are reviewed visually each UI change. The
  app is verified working end-to-end with the real local qwen-7b model.

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

---

## Maintenance round — audit + bug fixes (2026-06-10)

### 2026-06-10 — Parallel audit before touching anything

- **Ran a 7-agent parallel audit** (backend quality, frontend quality, one
  investigator per reported bug, plus a study of the `~/code/pi` harness)
  before implementing. Findings cross-checked against the source by hand; the
  pi study is written up in `specs/pi-harness-learnings.md`.

### 2026-06-10 — main.py split into main/wiring/schemas

- **What:** `main.py` (431 lines) split: endpoints stay in `main.py`; the glue
  (`get_or_create_running`, `make_task_runner`, `apply_team_graph`,
  `resolve_session`, `starter_team_graph`) moved to `wiring.py`; request DTOs
  to `schemas.py`. Wiring helpers are now public names (no leading `_`) since
  they're a module API; `tests/test_model_switch.py` patches
  `backend.wiring.resolve_model` accordingly.
- **Why:** ~300-line soft ceiling; the wiring is the most intricate logic in
  the HTTP layer and deserves to be reviewable/testable on its own.
- **Reversibility:** trivial (move functions back).

### 2026-06-10 — resume endpoint hardening + small cleanups

- **Resume with a deleted team is now 409**, not a silent empty-graph resume —
  an empty graph masked the data problem and broke the pin-at-launch
  expectation. Dead `latest_session_id_for_team` removed. `AgentRegistry` is
  properly typed via `TYPE_CHECKING` (no more `object`/type-ignores).
  Frontend: SSE edge-animation timers are cleared on unmount (no setState after
  unmount), session-launch failures now surface in the launch popover, model-id
  parsing centralized in `bareModelId()`.

### 2026-06-10 — Delegation routes through the target's RunningAgent (bug fix)

- **What:** `ask_agent` previously built a fresh throwaway agent for the
  target, so a delegated run was invisible — no lifecycle change, no transcript
  events, no persisted history (the user saw the edge animate and nothing
  else). `Delegator` now takes an async worker provider; targets are obtained
  via `runtime.obtain_worker` (the same get-or-create path the HTTP layer
  uses) and run through `run_once`, which streams events under the target's id
  and persists its history. The asker is set to `waiting-on-agent` during the
  consult. The parent's usage budget is threaded through (`usage` shared;
  per-agent tally credits only the child's delta).
- **Also (user report):** two tasks created at once were both injected into
  the lead immediately. There was no scheduling: each POST /api/tasks spawns a
  concurrent runner and both hit the same worker's `run_once` concurrently,
  interleaving one conversation history. A per-worker `asyncio.Lock` now
  serializes runs — an agent is one "person"; a second task (or a delegated
  question) waits until the current run finishes. Known cosmetic gap: the
  waiting task's status already shows `running` while it queues.
- **Deadlock backstop:** delegation acquires the target's run lock with a
  15-minute timeout (`DELEGATION_BUSY_TIMEOUT`); simultaneous mutual A⇄B
  delegation would otherwise deadlock. Timeout → `ModelRetry` ("busy") so the
  asker proceeds without the consult.
- **Reversibility:** the worker-provider seam is injected; tests script it.

### 2026-06-10 — Sectioned system prompt with capabilities + environment (pi-inspired)

- **What:** instructions are now ordered sections (persona → team context →
  tool guidance → capability summary), plus per-run `@agent.instructions`
  fragments: the named neighbor list and an environment block (agent id/name,
  repo root, OS, today's date) registered last so the freshest facts sit at
  the end — the pattern documented in `specs/pi-harness-learnings.md`.
- **Why (user report):** agents weren't told their persona context, links,
  cwd, OS — and notably not their *capabilities*: a read-only agent had no
  write tool but was never told, so it discovered limits via failed calls.
  `capability_summary()` states filesystem level, non-default globs, and bash
  availability up front.
- **Verified:** instructions delivery itself was never broken — Pydantic AI
  re-inserts `instructions` on every model request, including runs resumed
  with `message_history` (confirmed in the installed package + a request-
  capture test). The gap was content, not mechanism.
- **Reversibility:** all additive prompt sections; pure functions of spec/env.

### 2026-06-10 — Default model must have LM Studio tool_use capability

- **What:** live verification showed `qwen2.5-coder-7b-instruct-mlx` (the old
  default) emitting tool calls as TEXT in a code fence (`write_file(...)
  [END_TOOL_REQUEST]`) — LM Studio lists it without the `tool_use` capability,
  so nothing executes and the run "succeeds" having done nothing. Default
  switched to `lmstudio:qwen/qwen3.5-9b` (tool_use-capable, per the user's
  preference; `google/gemma-4-12b-qat` is the alternative). Tool guidance now
  explicitly forbids text/pseudo-code tool calls. LM Studio API notes saved in
  `specs/lmstudio-api.md` (v1 endpoints can load/unload models — one at a time
  on this laptop).
- **Reversibility:** model strings are per-agent config; edit in Capabilities.

### 2026-06-10 — Live playground test results (qwen/qwen3.5-9b)

- **End-to-end success:** a "create coin.py" task given through the web UI
  reached `done` in ~1 min and produced a correct, runnable file; an earlier
  rps.py task produced a working game + README via Lead → Documenter
  delegation, visibly animated and logged. The capability summary works as
  intended — the lead's thinking showed *"write_file isn't available"* and it
  planned around its limits instead of failing blindly.
- **Slow ≠ stuck:** the rps lead spent ~10 min on its final turn (qwen3.5 is
  a thinking model on a weak laptop). Patience first; the new finite read
  timeout (default 600s) catches genuinely dead connections.
- **Known gap (existing backlog):** the Agent tab transcript only shows live
  SSE events — page reload empties it even though history is persisted in
  `agent_state`. That's the "persisted work-log UI" Phase 9.4 item.

### 2026-06-10 — Blocked tasks get an in-place Retry (user request)

- **What:** `POST /api/tasks/{id}/retry` re-runs a *blocked* task as the same
  row — clears the stale result and hands the id back to a fresh `TaskRunner`
  (its first move is blocked → running, already a legal lifecycle transition).
  The task-detail drawer shows a `↻ Retry` button when status is blocked. The
  orphaned-task note now says "press Retry" instead of "re-create the task".
- **Why in-place, not clone-and-cancel:** keeps the task's identity, timeline
  and sub-task links; the lifecycle already allowed blocked → running so no
  state-machine change was needed. Retry is restricted to `blocked` (the only
  state runner errors, revision caps, and restart-orphans land in) — a 409
  otherwise prevents double-running an active task.
- **Reversibility:** one endpoint + one button; trivially removable.

### 2026-06-10 — uvicorn --reload wedging at "Waiting for connections to close"

- **What:** the user's recurring observation (reload starts, "Shutting down",
  then nothing) is uvicorn's *graceful* shutdown waiting for in-flight
  responses to finish — and the SSE `/events` stream never finishes by design
  (`EventBus.subscribe()` blocks on `q.get()` until the client disconnects).
  Any open control-room tab therefore blocks every code reload indefinitely.
  Closing the bus from the lifespan can't fix it: uvicorn runs lifespan
  shutdown *after* connections close, which is exactly the wait that's stuck.
- **Fix:** run with `--timeout-graceful-shutdown 3` (documented in CLAUDE.md).
  After 3s the SSE connections are force-closed; the browser's EventSource
  auto-reconnects to the new process, so the UI heals itself.
- **Reversibility:** a CLI flag; no code change.

### 2026-06-10 — Stop didn't stop task-driven runs (user report)

- **What:** `RunningAgent.stop()` only cancelled the inbox-loop task — but
  task and delegation runs go through `run_once`, awaited by the TaskRunner's
  own asyncio task. So Stop on an agent working a *task* cancelled nothing:
  the model kept generating and the task sat in `running` with no way back.
  Fix: track the in-flight `run_once` future (`_current_run`) and cancel it in
  `stop()` too; `TaskRunner` catches the `CancelledError`, parks the task
  `blocked` with "stopped by the user — press Retry", and re-raises.
- **Caveat:** cancelling closes our HTTP connection, but LM Studio may finish
  the in-flight generation server-side anyway (non-streamed request; nothing
  consumes the result). Streamed model requests would make disconnects bite
  sooner — noted as a future option.
- **Reversibility:** localized to runtime.stop/run_once + one except clause.

### 2026-06-10 — Local-model prompt hygiene (from a real LM Studio request dump)

- **What:** the user captured a full /v1/chat/completions body. Two problems:
  (1) `list_dir`/`grep`/`run_bash`/`write_file` had no docstrings → empty
  tool descriptions in the schema (small models pick tools by description);
  (2) pydantic-ai's default `'auto'` echoed `reasoning_content` (the
  thinking trace) back into every later request — pure context bloat; Qwen
  guidance says drop prior-turn thinking. Fixed: docstrings added, and the
  LM Studio model profile sets `openai_chat_send_back_thinking_parts=False`.
- **Reversibility:** docstrings are additive; the profile is one dataclass
  field in `models.py`.

### 2026-06-10 — Persisted work-log UI + Clear/Summarize (user request)

- **What:** the Agent tab now shows the agent's *real* model context, not just
  live SSE: `GET /api/agent/{id}/history` renders the stored conversation
  (same row shapes as the live events → one renderer) plus the system-context
  sections (persona/capabilities, neighbors, environment) in send order,
  collapsed at the top. Live events append after a `baseline` index recorded
  at fetch time — older events are already inside the stored history, so
  rendering both would duplicate. Two new actions, both 409 while the agent
  is mid-run: **Clear** (history → `[]`; identity survives because
  instructions are sticky and rebuilt every request) and **Summarize** (one
  model call summarizes the conversation; history becomes a synthetic
  user-summary + assistant-ack pair — the "summarize-oldest" variant
  history.py's docstring anticipated, but user-triggered).
- **Why a synthetic two-message pair:** pydantic-ai history must stay a
  well-formed request/response sequence; a user-role summary + short ack is
  the minimal valid shape a next run can resume from.
- **Known edge (pre-existing):** a run that *errors* persists no history
  (history_out only fills from run.result), so failed runs leave no stored
  transcript. Verified live on the playground session: the lead's full
  4-task history renders end-to-end.
- **Reversibility:** additive endpoints + one tab rework; renderer shared.

### 2026-06-11 — Live chat tail went blind after the events array reset (user report)

- **What:** the Agent tab's live tail used an array *index* (`events.length`
  at history-fetch time) as its cutoff. `useEvents` resets the events array on
  remount/session switch, so the index pointed past everything and every new
  event — including the user's own message — was silently hidden until a full
  reload. Fix: each event gets a module-scoped monotonic `seq` at arrival;
  the cutoff is "seq > baselineSeq", which survives array resets.
- **Lesson recorded:** never anchor "events after X" logic to array indices
  when the array can be replaced; anchor to a monotonic id.

### 2026-06-11 — Delegation failures were a generic "exceeded max retries"

- **What:** the user's retried task failed with `Tool 'ask_agent' exceeded
  max retries count of 1` and nothing visible anywhere. Root cause was
  environmental — the team had been switched to
  `deepseek-coder-v2-lite-instruct-mlx`, which lacks LM Studio's `tool_use`
  capability — but the product hid it. Two fixes: (1) `Delegator.ask` now
  publishes the failure as the a2a *reply* (`[consultation failed: …]`), so
  the canvas animation + message log + Links history show the real cause;
  (2) the Capabilities model picker explicitly labels non-tool_use models
  ("⚠ no tool calls") and shows an amber warning box when one is selected,
  instead of a subtle missing-🛠.
- **Reversibility:** one publish line + picker UI; no behavior change for
  successful delegations.

### 2026-06-11 — Live tail v2: anchor at the last COMPLETED run (user report #2)

- **What:** the seq fix wasn't enough — the user opened the Agent tab while a
  task run was already streaming, and the cutoff "everything before my fetch
  is old" hid the in-flight run's events (they're in neither the persisted
  history, which only updates at run END, nor the future). The tail now cuts
  at the last `agent_done`/`agent_error` *for that agent*: completed runs
  come from history, anything after the last completion is live. History is
  re-fetched ~400ms after each done/error so the transcript converges.
- **Why 400ms:** run_once persists in its `finally` right after publishing
  agent_done; the delay covers that ordering without polling.

### 2026-06-11 — ask_user: a structured question channel + anti-stall (user request)

- **What:** agents kept ending their turn by writing questions as plain text
  (nothing can answer those → the run dies, the work stalls — the wordle task
  did exactly this). Three-part fix:
  1. **`ask_user` tool** (`questions.py`): parks the run on an asyncio Future
     on the session-owned `QuestionBoard`, publishes a `user_question` SSE
     event, and resumes with the user's answers as the tool result. New
     lifecycle `waiting-on-user` (purple "needs you" node badge). Endpoints:
     `GET /api/questions`, `POST /api/questions/{id}/answer` (422 on count
     mismatch, 404 if the question died). Answer timeout 1h
     (`AGENT_GRAPHS_ASK_USER_TIMEOUT`) returns a "proceed on your best
     judgment" note instead of failing the run.
  2. **Prompt guidance** (persona TOOL_GUIDANCE): never end a turn with
     plain-text questions; keep working until done or genuinely blocked.
  3. **Continuation nudge** (`wiring.run_agent`): a task run that ends with
     open todos is re-prompted to continue (cap 2) — converts "accidentally
     stopped" into "kept working" without infinite-loop risk.
- **UI:** amber question card in the Agent tab (option buttons + free-form
  per question, one submit); verified end-to-end in a real browser via the
  new `scripts/scripted_backend.py` (FunctionModel-backed backend).
- **Reversibility:** tool + board are additive; the nudge is one loop in
  wiring with a constant cap; lifecycle value is additive.

### 2026-06-11 — Floating edges + edge selection → Links tab (user request)

- **What:** fixed left/right handles made A⇄B pairs overlap into one
  unreadable line (and drew loops when the target sat left of the source).
  New `FloatingEdge` type: anchors at the intersection of the center-to-center
  line with each node's border; reciprocal pairs get opposite ±18px
  perpendicular offsets so they render as two parallel arcs with arrowheads.
  Decoration happens at RENDER time in Canvas (type/marker/offset derived from
  the full edge list) — the persisted graph and graphMapping stay plain, and
  drawing a reverse edge separates the pair instantly. Handles remain only as
  connection-drag sources.
- **Edge selection was silently dropped:** `onSelectionChange` only read
  `nodes`. Now one selected edge sets the sidebar agent to the edge's SOURCE
  and passes `focusEdgeId` down — the sidebar jumps to the Links tab, the
  row highlights, and the "why" input autofocuses. Edge labels are
  pointer-transparent so clicks land on the selectable path beneath.
- **Reversibility:** one new component + render-time mapping; deleting the
  edgeTypes entry restores default edges.

### 2026-06-11 — Reciprocal edges DID overlap (sign bug) + draggable edge bends

- **What:** the floating-edge "reciprocal offset" shipped with a sign bug:
  the perpendicular axis flips with edge direction, so the canonical
  `source < target ? +1 : -1` factor CANCELED the natural flip — both edges
  of a pair landed on the same side (the verify assertion compared path
  *strings*, which differ for reversed-but-coincident curves; now it
  compares midpoint geometry). Fix: no per-edge sign at all — the same
  default offset applied in each edge's own frame separates the pair.
- **Draggable bends (user request):** every edge now has a midpoint dot;
  dragging it perpendicular routes the edge around clutter. The displacement
  persists as `GraphEdge.curve` (new backend field, default 0 = auto:
  straight, or the default arc for reciprocal pairs; dragging near straight
  snaps back to 0). The path is a quadratic THROUGH the displaced midpoint,
  with border anchors recomputed toward it so the attachment angle follows
  the bend.
- **Reversibility:** curve is one optional field with a safe default; the
  rendering change is contained in FloatingEdge/Canvas.

### 2026-06-11 — Intermittent empty canvas/links on page load (user report)

- **What:** "sometimes the links don't show until I refresh." Could not
  reproduce via 70 reload loops (incl. 6× CPU throttle, real stack) — the
  floating-edge rendering itself is sound. The actual hole: boot-critical
  fetches had NO retry. `App`'s session load, `refresh()` (teams+sessions),
  and `useTeamGraph`'s graph load each fire exactly once; if one fails —
  trivially common here because the backend runs under `--reload` and
  restarts whenever backend code changes — `activeTeamId` stays null and the
  canvas (nodes AND links) stays empty until a manual refresh, with no
  re-fire trigger. Fix: `withRetry` (6 × 700ms) around those three fetches,
  with cancellation guards. Verified the exact scenario in-browser: page
  loaded against a dead backend self-heals when the backend returns.
- **Also:** `useEvents` no longer opens `/events` without a session id (was
  a guaranteed 400 on every pre-reconcile boot).

### 2026-06-11 — Delegated edits always died: stale-hash errors were FATAL (user report)

- **What:** "lead asks the implementer, its window stays empty, nothing
  happens." The message log showed every consult failing identically:
  `[consultation failed: stale: the targeted lines changed…]`. Two compounding
  bugs + one prompt gap:
  1. **Tool errors killed runs.** `edit_file`'s stale rejection (and sandbox/
     access/regex errors) raised plain `ValueError` — pydantic-ai treats
     anything but `ModelRetry` as fatal, so the "re-read and retry" nudge the
     message was WRITTEN to be never reached the model; the target's whole run
     died and the asker got a dead consult. Fixed at the agent boundary
     (`capabilities._self_correcting` wraps every dev tool, ValueError →
     ModelRetry; DevTools stays framework-free) + `retries=3` per agent.
  2. **Failed runs persisted no history** (history_out only filled from
     run.result), so the post-error history refetch wiped the transcript —
     the user literally watched "a message appear, then everything
     disappears". The error path now hands back the partial message history
     and both run paths adopt it in `finally`.
  3. **The lead dictated edit hashes** ("Hash de8…") that are stale by
     definition for another agent. TOOL_GUIDANCE now says edit tokens are
     personal — read before editing, describe (don't dictate) delegated edits.
- **Not context overfill** (user's hypothesis): compaction caps history at 40
  messages; the failures were deterministic, on the first tool call.
- **Tests:** stale-hash + sandbox nudges at agent level, failed-run transcript
  persistence at runtime level, and the full delegation-recovery scenario at
  a2a level (the exact reported flow).

### 2026-06-11 — Source restructure: packages, not a flat module root

- **What:** Both source roots were flat (26 backend modules, 17 frontend
  files). Backend now groups by subsystem: `api/` (one endpoint module per
  resource, installed via closures over `app` — same style, just split out of
  main.py), `domain/` (pure shapes + graph validation), `runtime/` (Session,
  RunningAgent → workers.py, gateway, bus, streaming, tasks, usage),
  `agents/` (factory, persona, tools, capabilities, todos, a2a, questions,
  history), `providers/` (model resolution; LM Studio specifics split out of
  the old models.py/stats.py), `storage/` (db schema, teams, agent_state).
  `main.py` (boot only), `wiring.py` (composition root) and `util.py` stay at
  the backend root. Frontend: `lib/` (api, types, ui), `hooks/`, `canvas/`,
  `panels/` (+ `panels/tabs/`).
- **Why:** readability/maintainability ahead of the provider-abstraction work
  (a `providers/` home now exists), and CLAUDE.md guidance can live next to
  the code it governs (each new package has its own CLAUDE.md with that
  subsystem's invariants).
- **Key invariants preserved:** `uvicorn backend.main:app` entry unchanged;
  `DEFAULT_DB_PATH` still resolves to `backend/db.sqlite` (the user's data —
  storage/db.py computes parent.parent); stores keep living with their
  feature (task store in runtime/tasks.py, message log in agents/a2a.py).
- **Behavior:** zero change — moves + import rewrites only; 102 tests
  collected before and after, all green; verify_ui passes (twice).
- **Also:** hardened verify_ui's park-the-new-node drag (grab point could land
  under the widened sidebar → text-selection instead of a node drag; now
  verified + retried, selection cleared). **Reversibility:** git mv history is
  intact; moving files back is mechanical.

### 2026-06-11 — Pluggable model backends + DeepSeek with thinking control

- **What:** a `ModelBackend` abstraction (providers/base.py) with two
  implementations — LM Studio (existing behavior) and the DeepSeek API — a
  registry that resolves `"<backend>:<model>"` strings and maps per-agent
  thinking preferences to backend-specific settings, `/api/providers` +
  `/api/providers/{id}/models` endpoints, and a Capabilities-tab Backend
  dropdown above the model dropdown with thinking on/off + effort (high|max)
  controls that appear only when the backend supports them. AgentSpec gained
  optional `thinking`/`thinking_effort` fields (old persisted specs load
  unchanged — no migration).
- **Key choices:**
  - **Keys in a gitignored `config.yml`** (committed `config.example.yml`
    shape; env vars override) — verified `git check-ignore` BEFORE writing the
    real key; the working agreement in CLAUDE.md now mandates an `sk-` grep of
    staged diffs.
  - **Thinking is a request parameter, not a model id**: DeepSeek deprecated
    the deepseek-chat/deepseek-reasoner split (2026-07-24); we send the native
    `extra_body.thinking` object (`enabled/disabled`, `reasoning_effort`
    high|max). pydantic-ai's DeepSeek profile already passes prior
    `reasoning_content` back (the v4 API requires it in tool loops) — the
    exact opposite of our LM Studio profile, deliberately.
  - **The resolver seam is untouched** (`resolve_model(str) -> Model`) so all
    FunctionModel test plumbing and `wiring.resolve_model` monkeypatches keep
    working; thinking travels separately via `thinking_settings(...)` →
    `Agent(model_settings=...)` (workers, reviewer, summarizer).
- **Verified live**: real DeepSeek list-models (v4-flash + v4-pro), a 1-token
  chat call (thinking disabled), and a thinking call (ThinkingPart + 25
  reasoning tokens, effort accepted). UI verified in-browser: backend switch,
  thinking controls, persistence to the team graph, friendly degraded states
  (no key / LM Studio down).
- **Reversibility:** the abstraction is additive; deleting providers/deepseek.py
  + the registry entry reverts to LM Studio-only.

### 2026-06-11 — Agents load AGENTS.md/CLAUDE.md project context like Claude Code

- **What:** when an agent reads a file, `read_file` now prepends the context
  files governing it — for each directory from the session repo root down to
  the file's directory, `AGENTS.md` if present else `CLAUDE.md` (AGENTS.md
  shadows CLAUDE.md per directory, supporting both tool ecosystems) — each
  wrapped in `[project context from <path> — applies ONLY to <scope>]`
  delimiters, root-first. Matches Claude Code's lazy/additive subdirectory
  memory behavior (researched: docs + community writeups).
- **Key choices:**
  - **Once per conversation**, tracked by a `ProjectContext` owned by the
    `RunningAgent` and reset on history clear/summarize — the blocks live in
    the message history, so a cleared/compacted conversation must be able to
    receive them again. A worker rebuild (spec change) also resets; re-injection
    is harmless.
  - **Blocks are PREPENDED** to the read result so the edit-token stays the
    last line (models copy the trailing token into edit_file).
  - Reading a context file directly marks it seen without a duplicate block;
    per-file 10k-char cap (weak local models); a broken/unreadable context
    file never fails the read. DevTools without a tracker (bare construction
    in tests) injects nothing — injection is explicit, per the DI style.
  - TOOL_GUIDANCE tells the model what the blocks are and that they are
    folder-scoped.
- **Tests:** pure lookup order + shadowing, delimiters/scope wording,
  once-per-conversation dedup, self-read, reset, truncation, and an
  end-to-end RunningAgent run (inject → no repeat → clear → re-inject).
- **Reversibility:** drop the `project_context` kwarg wiring and the module.

### 2026-06-12 — Context-file guidance must forbid proactive reads (live fix)

- **What:** the first live run after the AGENTS.md/CLAUDE.md feature showed the
  implementer opening its turn with `read_file AGENTS.md` + `read_file
  CLAUDE.md` in a repo that has neither ("not a file" retries). The harness
  never forced those reads — naming the files in TOOL_GUIDANCE was enough for
  qwen3.5-9b to seek them out (its thinking echoed the section heading).
  Injection itself was always Python-side (DevTools.read_file) and working.
- **Fix:** reworded the persona section — blocks are "injected for you
  AUTOMATICALLY", plus an explicit "Do NOT seek out or read AGENTS.md /
  CLAUDE.md files yourself". Recorded as an invariant in agents/CLAUDE.md.
- **Considered & rejected:** emitting the guidance only when the repo actually
  contains context files (needs a per-run rglob over the repo — costly on big
  trees). Escalate to that only if the wording fix proves insufficient.
- **Reversibility:** wording-only.

### 2026-06-13 — Phase 1: Harness abstraction + NativeHarness (OpenCode integration)

- **What:** introduced `backend/harness/` — a `Harness` ABC keyed by `agent_id`
  that every agent operation (run/interject, run-to-completion, stop, history/
  clear/summarize, ask_user, usage, delegate) routes through. `NativeHarness`
  wraps today's pydantic-ai machinery with ZERO behavior change; the API +
  `wiring.make_task_runner` now call `session.harness.*`. `SessionManager`
  builds the harness per session (persisted `sessions.harness` column, default
  from config; additive non-destructive migration for existing DBs).
- **Why:** the seam that lets a session run on either our harness or an
  OpenCode-backed one, switchable per session, side by side.
- **Key decisions:**
  - Interface keyed by agent_id (no `RunningAgent` leaks) — OpenCode has no such
    object.
  - `session.bus` + `session.registry` stay UNIVERSAL (the event/lifecycle
    contract both harnesses publish identically); `gateway/usage/questions`
    become native-harness internals the OpenCode harness leaves idle. Kept them
    on Session (unused-for-opencode) rather than moving, to minimize Phase-1
    risk.
  - `wiring.resolve_model` re-exported and resolved at call time by the native
    harness, so the long-standing `monkeypatch.setattr(wiring,"resolve_model")`
    test seam is unchanged — zero test churn beyond pointing one white-box test
    (test_model_switch) at `session.harness._worker`.
  - `Harness.delegate()` implemented once on the base (shared `check_delegation`
    guards + run_to_completion) — native agents still use their in-process
    ask_agent tool; this base path is what the OpenCode ask_agent callback will
    reuse, so guard behavior is identical across harnesses.
- **Verified:** 130 tests green (was 122 + 8 new harness-seam tests); live
  backend reloaded clean, existing session reports harness=native through the
  full stack; migration proven idempotent on a copy of the real db.sqlite.
- **Reversibility:** additive; the native path is the old code behind a thin
  interface.

### 2026-06-13 — Phase 2: OpenCode config generation + server lifecycle

- **What:** `backend/harness/opencode/` — `config.py` builds `opencode.json`
  from a TeamGraph (one OpenCode agent per AgentSpec, model id translation,
  capability→permission mapping, OpenCode-flavored prompt, the ask_agent.ts
  delegation tool); `server.py` (`OpenCodeServer`) spawns one `opencode serve`
  per session, waits for readiness, supports reconfigure (restart on graph
  change) + clean shutdown.
- **Key decisions:**
  - **Don't litter the user's repo**: the server's cwd is a dedicated temp
    *config home* (opencode.json + .opencode/tool/ live there); agent SESSIONS
    are scoped to the repo via `POST /session?directory=<repo>`. Verified live:
    file ops happen in the repo, config stays out of it.
  - Can't reuse `build_instructions` verbatim — its tool guidance names native
    tools (ask_user/write_file/edit-tokens) that don't exist in OpenCode. A
    separate `build_opencode_prompt` reuses the shared identity (persona, team
    context, capability summary, neighbors, environment) + OpenCode-specific
    tool guidance (read/edit/write/bash/`question`/`ask_agent`).
  - Permission mapping sets **`question: allow`** (OpenCode defaults DENY → would
    kill ask_user) and **`task: deny`** (we delegate via our own ask_agent, not
    OpenCode subagents); webfetch/websearch denied to match native.
  - ask_agent.ts reads its callback wiring (URL, token, our session id) from env
    the server injects; passes `ctx.agent` (our agent id) as the asker.
- **Verified:** 9 pure config-gen unit tests; LIVE boot confirmed both agents
  load with correct model/permissions, ask_agent registered, repo-scoped
  session created, clean shutdown. Full suite 138 green. Live server boot is
  manual (needs the binary) — not in the fast suite, like test_live_smoke.

### 2026-06-13 — Phase 3 live fix: OpenCode prompt_async requires cwd == session dir

- **What:** the OpenCodeHarness live run hung with 0 messages. Root cause
  (proven, model-independent via qwen3-1.7b): OpenCode's `prompt_async` only
  starts a run when the server's cwd equals the session directory; the
  config-home-cwd + `?directory=repo` architecture silently no-ops (issue
  #12271). NOT a model or harness-logic bug (fake-server tests were always
  green).
- **Fix:** server.py now runs `opencode serve` with cwd = the repo, config via
  `OPENCODE_CONFIG_CONTENT` (no opencode.json in the repo), tool at
  `<repo>/.opencode/tool/ask_agent.ts`, and removes a `.opencode` we created on
  shutdown (incl. the bun-installed node_modules). Live-verified: hello.txt
  created, all bus events streamed, repo clean after.
- **Also found:** OpenCode doesn't know the `deepseek-v4-flash` id (only
  models.dev ids / its gateway free model) — documented as a config-gen TODO
  for the opencode harness + DeepSeek; native harness unaffected.
- Full suite 144 green (fake-server tests unaffected by the server change).

### 2026-06-13 — Phase 4: ask_user (questions) for OpenCode

- Translate OpenCode question events: `question.asked` → cache + `user_question`
  bus event + waiting-on-user lifecycle; `question.replied`/`.rejected` →
  `user_question_done` + back to running. `list_questions` reads the cache (sync,
  no round-trip); `answer_question` (now async across the Harness interface →
  the answer endpoint awaits it) maps our one-string-per-question to OpenCode's
  `{answers: string[][]}` and POSTs the reply.
- Verified via the fake: a parking `question` turn surfaces a listable question
  with mapped options + waiting-on-user, and answering it resumes the run to
  completion + clears it (user_question/user_question_done on the bus). 145 green.

### 2026-06-13 — Phase 5: ask_agent delegation for OpenCode

- The OpenCode-side ask_agent.ts tool POSTs to a new `POST /internal/ask_agent`
  (api/internal.py): localhost callback authenticated by a per-session token the
  harness injects into the server's env. The endpoint resolves the session,
  verifies the token, and calls `Harness.delegate` — the shared base path
  (check_delegation guards → waiting-on-agent + a2a_message → run the target on
  its persistent OpenCode session → reply). Guard violations come back as 409
  with the corrective message (the tool surfaces it to the model).
- Per-session harness choice now flows from launch (`LaunchSessionRequest.harness`
  → create_session) and persists.
- Verified: opencode delegate() via fake (lead→expert, a2a_message + message log,
  neighbor-guard rejection); the /internal/ask_agent endpoint via TestClient
  (token 403, valid 200 with the target's answer, non-neighbor 409, unknown
  session 404) — built through the real create_app path with the fake connection.
  148 green. Full live ask_agent loop (model → ask_agent.ts → endpoint) is a
  documented manual step (the tool→callback-with-identity leg was proven in the
  earlier smoke test; endpoint→delegate→target is now unit+integration tested).

### 2026-06-13 — Phase 6: mocked E2E parity for OpenCode (no LLM/server)

- tests/_fake_opencode.py (deterministic in-process fake) + suites covering the
  OpenCode harness at every level: run/submit/history/usage/stop/reviewer/nudge
  + ask_user park-resume + delegate (test_opencode_harness.py), the
  /internal/ask_agent endpoint (test_internal.py), and a full API-level task run
  through create_app (test_opencode_e2e.py: launch opencode session → task →
  done → history rows + usage via the same HTTP surface as native). 149 green,
  deterministic, no model or subprocess.

### 2026-06-13 — Phase 7: frontend harness toggle

- Onboarding gains an "Agent harness" select (native | opencode) → launchSession
  passes it; SessionInfo carries `harness`; the header shows an "opencode" chip
  for non-native sessions. The control room is harness-agnostic (both publish
  identical bus event shapes), so no render special-casing. verify_ui asserts
  the selector renders with both options and the native launch flow stays green
  (OK). Build green. Full live opencode-via-UI run is a documented manual step
  (the badge is trivial conditional render; the backend opencode path is
  exhaustively tested).

### 2026-06-13 — Phase 8a: OpenCode harness robustness (run timeout + reconfigure)

- **Run never hangs**: run_to_completion now bounds the session.idle wait
  (AGENT_GRAPHS_OPENCODE_RUN_TIMEOUT, default 900s); on timeout it aborts the
  OpenCode run, publishes agent_error, and lands the agent blocked — never a
  stuck awaiter if the server dies / the SSE stream drops.
- **Graph edits take effect (parity with native's spec_changed rebuild)**:
  _ensure compares the graph signature and, on change, reconfigures the server
  (restart with new config) + drops the per-agent OpenCode sessions. Caveat: the
  OpenCode-side conversation is lost on reconfigure (server restart), heavier
  than native's history carry-forward — documented. An ensure-lock also fixes a
  concurrent-first-run double-spawn race.
- Tests: reconfigure-on-edit (and no-op on unchanged graph) via the fake. 150 green.

### 2026-06-13 — Phase 8b: adversarial-review fixes (19-agent review)

A multi-agent review (parity/resources/security/correctness/coverage, with
adversarial verification) confirmed real issues; fixed the substantive ones:
- **Stop → CancelledError** (high): a stopped OpenCode task-run returned
  normally, so the TaskRunner marked it done instead of parking blocked. stop()
  now sets an `aborting` flag → run_to_completion raises CancelledError (Retry-
  able), and the abort's session.idle is suppressed (no spurious agent_done).
- **Listener-death frees awaiters** (high): if the SSE stream drops mid-run the
  awaiter hung forever; the listener now sets st.error + idle for pending
  agents on unexpected stream end. Plus the run timeout from 8a.
- **Cross-hop delegation guard** (high): the chain wasn't threaded across the
  ask_agent HTTP callback, so depth/cycle caps never accumulated (unbounded
  A→B→C…). run_to_completion stashes the chain on _AgentState;
  /internal/ask_agent reads it via current_chain() and passes it to delegate().
- **Usage** (high): input was summed across messages (O(N²) double-count of the
  re-sent context) and reasoning tokens were dropped — now input = latest
  message's context, output += reasoning.
- **Lifespan teardown** (resources): the lifespan only stopped native workers,
  leaking OpenCode servers/temp dirs — now calls `harness.shutdown(session)`
  for every session.
- **Failed start() cleanup** (resources): a boot timeout leaked the subprocess/
  client/log/staged tool — start() now tears down on failure before re-raising.
- **answer_question count check** (low): raises ValueError on count mismatch
  (→ 422), matching native/the ABC contract.
- **DeepSeek model decl**: config now declares deepseek models under
  provider.deepseek.models (best-effort registry fix; verify live).
- Reconfigure-on-edit (8a) already addressed the review's "graph edit not
  applied" finding. New tests: error path, stop-cancels, listener-death,
  answer-count, current_chain. 155 green.
