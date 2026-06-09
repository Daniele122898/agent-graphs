# Development Plan: A Local Multi-Agent Software Team in a Folder

## What we're building (the end state)

This is **a persistent, multi-agent software team that lives in a repo folder** — a group of long-lived specialized workers who share one workspace, hold specialized knowledge, delegate tasks to one another, and modify a real codebase autonomously and uninterrupted. The web app is a **control room** for watching and steering that team — it is not the thing doing the work.

Five principles flow from that framing and shape every decision below:

1. **Agents are long-lived background processes, not request/response handlers.** A coding agent working uninterrupted is a loop that runs for minutes — many model calls, many tool calls, no human in the middle. Each agent is a background task with its own lifecycle (running / waiting-on-another-agent / blocked-for-input / done). The UI *observes and interjects*; it does not drive turn-by-turn.
2. **The folder is the shared state, and access is per-agent.** All agents work in one repo. The graph defines *who talks to whom*; each agent's **capability profile** defines *what it can touch* (filesystem level + path globs, bash, and which model powers it). Separation of concerns becomes separation of access, enforced in the tool layer — an agent that shouldn't write code never receives a write tool.
3. **Model choice is per-agent and provider-agnostic.** Every agent independently runs on a hosted endpoint (Claude, GPT, etc.) or a local LM Studio model. Cheap "expert lookup" agents can run local; "working" agents that need strong tool-calling run hosted. The architecture assumes neither.
4. **A team is a reusable *definition*; a session is a *running instance* of that team on a repo.** One team definition (graph + personas + capabilities + links) can be launched as many sessions, each bound to a different repo, all running concurrently in one backend process. This template-vs-instance split is the spine of the data model and runtime — designed in from day one even though the multi-session UI comes later.
5. **Work enters as a Task and flows along the graph you drew.** You are the orchestrator at design time — the graph *is* the org chart — so the runtime doesn't discover the team, it routes a tracked Task object to an entry-point agent that decomposes it (a todo list), delegates along its links, and reports completion. A per-task completion signal (self-reported / reviewer agent / programmatic check) decides when a task is truly done. Code-level caps prevent the runaway-loop failures that sink most multi-agent systems. *Separately and beneath this*, an LLM execution gateway serializes model calls when running on a low-spec single-model machine — a hardware concern that the task system stays unaware of.

## TL;DR
- **Python/FastAPI backend + Pydantic AI agent harness + React + Vite + React Flow frontend + AG-UI/SSE streaming, with each agent as a long-lived async task.** Models are per-agent and provider-agnostic (Claude/GPT/local LM Studio) — this is free with Pydantic AI since a model is just config.
- **Pydantic AI ships NO local dev tools.** Its `builtin_tools` run on the *provider's* servers, not your machine, and don't apply to local models at all. You build a small dev toolset (Pi-style **read/write/edit/bash** core + grep/find) yourself or adopt `pydantic-ai-backend`'s Console Toolset. The toolset is **generated per-agent from its capability profile**, so agents only ever see the tools they're allowed. The `edit` tool is the highest-leverage piece — a line-range edit with a content-hash check (not `str_replace`) for context-efficiency and stale-edit rejection.
- **Sandbox = repo-root path-check + cwd (your choice), plus per-agent read/write path globs and a momentary filesystem write-lock.** No permission prompts — agents work uninterrupted inside their box. Sticky persona via `instructions`; compaction-safe via `history_processors`. Serial execution on low-spec machines via the LLM execution gateway (one `asyncio.Semaphore(1)`). No auth (local only).
- **Teams (definitions) and sessions (running instances) are separate from day one.** One backend process holds a `dict[session_id, Session]`; each `Session` owns its own agent registry, LLM execution gateway, write-lock, and event bus, bound to one repo. Multiple repos run simultaneously with zero process orchestration. The MVP auto-creates one team + one session and hides the concept; the data model and runtime carry `team_id`/`session_id` throughout regardless.
- **Tasks are first-class objects with a status lifecycle and a per-task completion signal.** A `tasks` table (queued → running → blocked → needs_review → done/failed) drives a session-level **task board** and snapshot/resume. Agents track progress via an industry-standard `write_todos` tool rendered as a live checklist. Completion is decided per task: self-reported, a reviewer agent (evaluator-optimizer), or a programmatic check like `pytest` (Anthropic's "gate"). Hard caps on turns and delegation depth kill runaway loops. **This system is about *what work exists and its state* — independent of hardware.**
- **A separate LLM execution gateway decides *how model calls dispatch against compute*.** Every model call — agent turns, `ask_agent` delegations, reviewer gates, compaction summaries — funnels through one gateway, toggled per session: parallel mode passes straight through; serial mode (low-spec, one model loaded) allows one in-flight call at a time via `asyncio.Semaphore(1)`. It sits *below* the task system and is invisible to it. **This is an infrastructure concern, distinct from the task system — do not conflate them.**

## Key Findings

1. **Pydantic AI has no built-in local filesystem/bash tools — and that's fine.** Its `builtin_tools` (`WebSearchTool`, `CodeExecutionTool`, `FileSearchTool`, `ImageGenerationTool`, `WebFetchTool`) are *provider-native* — executed on OpenAI/Anthropic/Google infrastructure, not your computer, and unavailable for local LM Studio models. For local dev work you build function tools (trivial) or use a third-party toolset. The framework is the *harness for wiring tools up*, which is exactly what you want.

2. **Capabilities should be generated per-agent, not shared.** Pydantic AI builds an agent's available tools from a `toolset`, and toolsets can be built dynamically. So each agent's toolset is assembled from its capability profile: a read-only agent literally never receives `write_file`; a no-bash agent never receives `run_bash`. The model never sees tools it can't use — cleaner and cheaper than letting it try and rejecting. Filtering/prepared toolsets and the `@agent.toolset` dynamic builder make this first-class.

3. **React Flow (@xyflow/react) is the right graph library.** Purpose-built for editable node graphs with custom React nodes, MIT-licensed, the de-facto standard for visual agent builders (Langflow, Flowise, Open Agent Builder all use it). Cytoscape.js/Sigma.js are visualization libraries for large static graphs, not editable node editors — wrong tool here.

4. **Pydantic AI is the best harness for this app.** Python (your preference), provider-agnostic (hosted + local in one line), streams thinking + tool events (`agent.run_stream_events` / `agent.iter`), native agent delegation for agent-to-agent calls, native AG-UI output, and — critically — `instructions` vs `system_prompt` plus `history_processors` that make sticky-persona-on-compaction trivial. LangGraph is the main alternative (more powerful state machine, more boilerplate).

5. **Stick with LM Studio for local; support hosted endpoints equally.** LM Studio exposes an OpenAI-compatible API at `localhost:1234` plus a richer REST API returning exactly the STATS you want (`max_context_length`, `loaded_context_length`, quantization, tokens/sec, TTFT). Hosted endpoints are just a different model string. **Caveat:** local-model tool-calling quality varies a lot; dev-working agents will likely need hosted frontier models, while local models suit cheap expert-lookup agents.

6. **For agent-to-agent communication, a delegation "ask" tool beats a heavyweight protocol.** Google A2A / MCP solve cross-vendor, cross-network interop you don't have. A simple in-process `ask_agent(target, question)` tool with a thin structured envelope (sender, target, question, optional context) and free-form natural-language body is simpler, fully observable, and sufficient. Each agent's live neighbor list is injected into its instructions so it always knows who to ask and why.

## Details

### Recommended Tech Stack

| Layer | Recommendation | Why | Main Alternative |
|---|---|---|---|
| **Backend** | **Python + FastAPI + uvicorn** | Native SSE, async-first, canonical pairing with Pydantic AI; terminal-start. | Node/TS + Hono + Mastra |
| **Agent harness** | **Pydantic AI** | Provider-agnostic, streams thinking+tools, agent delegation, sticky-instructions, dynamic per-agent toolsets, native AG-UI. | LangGraph (more powerful/complex); Agno (fast) |
| **Local dev tools** | **Hand-rolled `dev_toolset`** (bash, read/write/edit/grep/ls) | ~50 lines, zero deps, full control, generated per-agent from capability profile. | `pydantic-ai-backend` Console Toolset; `pydantic-deep` filesystem backend; MCP filesystem server |
| **Graph/node editor** | **React Flow (@xyflow/react)** | Built for editable node graphs w/ custom React nodes; industry standard. | Svelte Flow |
| **Frontend** | **React + Vite + TypeScript** | React Flow + AG-UI clients are React-first; Vite = trivial terminal-start. | SvelteKit |
| **Chat/streaming UI** | **AG-UI over SSE** (CopilotKit optional) | Pydantic AI emits AG-UI natively; renders streaming text, thinking, tool calls. | Hand-rolled SSE + assistant-ui |
| **Local inference** | **LM Studio** (keep) + any hosted endpoint | OpenAI-compatible + rich stats REST; hosted = just a model string. | Ollama (headless) |
| **LLM execution gateway** | **`asyncio.Semaphore(1)`** (serial) / pass-through (parallel) | In-process serialization of *model calls* on low-spec machines; distinct from the task system. No Redis/Celery for local single-user. | — |
| **Persistence** | **SQLite** | Zero-config, file-based; stores graph, personas, capability profiles, chat/work logs. | JSON files |

### Teams & Sessions (the multi-repo spine)

The single most important structural decision, designed in early because it's nearly free now and brutally expensive to retrofit. It rests on separating a **definition** from a **running instance**:

- **A team is a *definition* (template).** The graph topology plus each agent's persona, capability profile, model choice, and links. Static, small, serializable to JSON, **repo-agnostic**, and **reusable**. "A React expert connected to an implementation agent that writes `src/**`."
- **A session is a *running instance* of a team bound to a repo.** The live agent tasks, their conversation histories and compacted context, the inter-agent message log, current lifecycle. Dynamic, potentially large, tied to one repo on disk.

This is the image-vs-container / class-vs-object pattern. **One team spawns many sessions** — your "web-dev squad" template launched against three repos is three sessions sharing one definition. The three asks decompose cleanly: *save/load graphs* → CRUD on team definitions; *run multiple at once in different repos* → multiple sessions; *work in different repos simultaneously* → sessions are repo-scoped, teams are reusable across them.

**Runtime: one process, many in-memory sessions (recommended over multi-process).** A single FastAPI/uvicorn process holds a `dict[session_id, Session]`. Each `Session` owns *its own* agent registry, LLM execution gateway, write-lock, event bus, and repo binding. Because agents are already independent `asyncio` tasks and the work is I/O-bound (waiting on model APIs and disk), one event loop handles many concurrent sessions trivially — no CPU contention, models run elsewhere (hosted, or in LM Studio's own process). One terminal, one DB, one place to manage everything; true simultaneous multi-repo work with zero process orchestration. (Separate-process-per-session buys fault isolation you don't need locally at the cost of a fragmented experience — fragmented DBs, manual ports, N tabs. If fault isolation ever matters, a session can later move into a subprocess without changing the abstraction.)

```
App (one uvicorn process)
├─ TeamStore         — team definitions (templates), SQLite. Reusable, repo-agnostic.
├─ SessionManager    — dict[session_id, Session]
│   ├─ Session A → team "web-dev-squad" + repo /path/projectX
│   │   ├─ repo_root, write_lock, gateway(mode), event_bus   ← per-session, NOT global
│   │   ├─ AgentRegistry — dict[agent_id, RunningAgent] (tasks, lifecycle)
│   │   └─ message log + per-agent histories  → persisted, keyed by session_id
│   ├─ Session B → team "web-dev-squad" + repo /path/projectY   ← same team, different repo
│   └─ Session C → team "data-pipeline"  + repo /path/projectZ
└─ SSE endpoints — every event tagged with session_id; frontend subscribes per session
```

**Nothing is a global singleton.** The write-lock, LLM execution gateway, registry, event bus, and SSE bus are all **owned by a `Session`**, not module-level globals. The write-lock especially must be per-session (per-repo) — a global lock would wrongly serialize writes across unrelated repos. The gateway's serial mode likewise becomes a **per-session** choice: a low-power local-model session can serialize its model calls while a hosted-model session runs them in parallel.

**Four tables instead of one blob:**
- **`teams`** — definition: name + graph (nodes/edges) + per-agent persona/capabilities/model/links/`is_entry_point`. Repo-agnostic, reusable.
- **`sessions`** — binds `team_id` + `repo_path` + execution mode (parallel/serial) + status, keyed by `session_id`.
- **`agent_state`** — per-`(session_id, agent_id)`: conversation history, compacted context, lifecycle, usage. This is the "whole context and state" you want saved → enables snapshot/resume (pause repo X today, resume tomorrow with full history). *Written continuously as state changes (from Phase 3); the resume-and-rehydrate logic + UI is the only part deferred to Phase 9.*
- **`tasks`** — per-`session_id`: the work items, their status lifecycle, todos, completion signal, and delegation tree (detailed in Tasks & Progress Tracking below). Drives the task board and mid-task resume. (The task system is independent of the LLM execution gateway — see both sections below.)

**Three deliberate design rules:**
1. **A session copies (or pins an immutable version of) the team definition at launch.** Editing a template later does not mutate running sessions — they keep what they started with. Live-editing a *running* agent's persona is a separate, explicit action on the *session's* agent, not on the template. (Image/container model again: rebuilding the image doesn't restart your containers.)
2. **Same repo, two sessions: warn but allow.** On session creation, check whether another active session binds that `repo_path`; flag it (two task forces will fight over files) without hard-blocking.
3. **Two distinct verbs the data model must keep separate even if the MVP UI blurs them:** *open a team for editing* (template editor) vs. *launch a session from a team onto a repo* (the running control room).

**Cost of designing this in now:** one `session_id`/`team_id` column on each table and a `Session` wrapper object that owns what would otherwise be globals. The MVP auto-creates exactly one team and one session at startup and never surfaces the concept — zero extra UI. That is the entire price of turning "add multi-repo support later" from a core rewrite into an afternoon of frontend work.

### The Agent Model

Each agent is configured by:

```
Agent
├─ persona         (sticky system prompt — WHO they are)
├─ model           (claude / gpt / local LM Studio — per agent)
├─ is_entry_point  (bool — can receive tasks directly; ≥1 required per team)
├─ capabilities    (WHAT they can touch)
│   ├─ filesystem: none | read | read-write   ← simple level (default)
│   ├─ read_paths:  ["**"]        ← advanced override (globs within repo root)
│   ├─ write_paths: ["src/**"]    ← advanced override
│   └─ bash: on | off
├─ links           (WHO it may delegate to, + why)
└─ lifecycle       (idle / running / waiting-on-agent / blocked / done)
```

The simple level is a preset that fills the globs (`read-write` → `read_paths:["**"], write_paths:["**"]`); an "advanced" disclosure lets you override per path. **Capability is the same underlying data as the level** — the level is just a one-click preset. **`is_entry_point`** marks which agent(s) can be handed a task directly (the "lead"); a team requires at least one, and new tasks default their `assigned_agent_id` to it. Lifecycle is the same five states used throughout: `idle / running / waiting-on-agent / blocked / done`.

### Agent Tools (simple core, context-efficient edits)

Follow Pi's proven minimalism: a small core of **read, write, edit, bash** plus **grep/find**, and resist bloat — every other capability is a composition of these. But one finding dominates the design: **the edit-tool *format* matters more than the model.** A widely-cited 2026 benchmark showed Grok Code Fast jump from 6.7% → 68.3% edit success purely by changing the edit format — a ~10× swing with no model change. The edit tool is therefore the single highest-leverage piece of the whole harness, and it's also where your "targeted edits to limit context" requirement lives.

Two halves to limiting context:

1. **`read` takes a line range and returns numbered lines** (`read(path, start_line?, end_line?)`, default-capped à la Pi's ~3000-line cap). The agent pulls only the slice it needs, not the whole file.
2. **`edit` targets lines, not reproduced text.** The three formats in the wild, worst→best for this app:
   - *`apply_patch`/diff strings:* model-specific; high failure off-target models. Avoid.
   - *`str_replace` (Claude Code, Pi's original):* find exact old text incl. whitespace, replace. Simple, but the model must **re-emit surrounding code to make the match unique** (a context cost), and one indentation mismatch fails → retry loops that burn more context. This is what the earlier draft assumed; it's the thing to move away from.
   - *hashline / hash-anchored edits (oh-my-pi's headline feature):* each line gets a short content-hash anchor on read; the model references the **anchor** instead of reproducing text. Benchmarked across 16 models/180 tasks: matches or beats `str_replace` almost everywhere, weakest models gain most, one model used **61% fewer output tokens** as retry loops vanished. Bonus property that matters *specifically because multiple agents share one repo*: **if the file changed since last read, the anchors don't match and the edit is rejected before corrupting anything** — free optimistic-concurrency that guards against editing a stale view (a *logical* race the write-lock can't catch).

**Recommended staging (balancing your "keep tools simple" instruction):** v1 ships a **line-range edit** — `edit(path, start_line, end_line, new_content)` — with a **content-hash check on the targeted lines** as the safety layer. This captures most of the context savings (no re-emitted surroundings) and the staleness guard, without building the full anchor-reference protocol. Graduate to full hashline anchors later if edit reliability on your local models proves shaky. Reference implementation to study: **oh-my-pi** (`edit` with LINE#ID anchors). Keep the rest of the core boringly simple — `write` creates/overwrites (auto-creating parent dirs), `bash` runs with `cwd=root` and truncates oversized output with a pointer to the full log (Pi truncates >~50KB/2000 lines), `grep`/`find` for discovery.

### The Sandbox (your choice: path-check + cwd)

Two layers, both enforced in the tool layer, no permission prompts:

- **Hard outer boundary (everyone):** every file/bash tool takes the agent's repo `root`; every path is resolved and checked to stay within it (`Path(p).resolve().is_relative_to(root)`); `run_bash` runs with `cwd=root`.
- **Soft inner boundary (per-agent):** read/write path globs from the capability profile, checked per tool call. Read-only agents never receive write tools at all.
- **Momentary write-lock:** a single `asyncio.Lock` held only during an actual write/edit op (not for a whole task), so concurrent writes can't produce half-written files even in parallel mode. Reads need no lock. (In the gateway's serial mode, model calls already can't overlap, so write contention is rarer — but the lock is independent of the gateway and stays correct in either mode.)

```python
def make_dev_toolset(root: Path, caps: Capabilities) -> FunctionToolset:
    ts = FunctionToolset()
    def _safe(p):
        full = (root / p).resolve()
        if not full.is_relative_to(root): raise ValueError("outside repo")
        return full
    if caps.filesystem in ("read", "read-write"):
        @ts.tool_plain
        def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
            if not any(fnmatch(path, g) for g in caps.read_paths): raise ValueError("no read access")
            return numbered_slice(_safe(path), start_line, end_line)  # numbered lines, capped
        @ts.tool_plain
        def list_dir(path: str = ".") -> str: ...
        @ts.tool_plain
        def grep(pattern: str, path: str = ".") -> str: ...
    if caps.filesystem == "read-write":
        @ts.tool_plain
        async def write_file(path: str, content: str) -> str:
            if not any(fnmatch(path, g) for g in caps.write_paths): raise ValueError("no write access")
            async with WRITE_LOCK: _safe(path).write_text(content)
            return "ok"
        @ts.tool_plain
        async def edit_file(path: str, start_line: int, end_line: int,
                            new_content: str, lines_hash: str) -> str:
            if not any(fnmatch(path, g) for g in caps.write_paths): raise ValueError("no write access")
            async with WRITE_LOCK:
                cur = _safe(path).read_text().splitlines()
                if hash_lines(cur[start_line-1:end_line]) != lines_hash:
                    raise ValueError("stale: re-read before editing")  # optimistic-concurrency guard
                cur[start_line-1:end_line] = new_content.splitlines()
                _safe(path).write_text("\n".join(cur))
            return "ok"
    if caps.bash:
        @ts.tool_plain
        def run_bash(command: str) -> str:
            r = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=root)
            return f"exit={r.returncode}\n{r.stdout}\n{r.stderr}"
    return ts
```

> **Honest caveat:** path-check + cwd is *not* an escape-proof security boundary — a `run_bash` command using absolute paths or `cd ..` can leave the repo. You accepted this for v1 (local, single-user, you define the agents). If you later want true non-interference on untrusted tasks, run each agent's bash in a Docker container with only the repo mounted — the tool interface stays identical, only the executor changes. Easy to graduate to.

### The Five-Tab Sidebar

Each tab owns one question; configure-vs-observe is cleanly split:

| Tab | Question | Read/Write | Backing |
|---|---|---|---|
| **Persona** | *Who* is this agent? | write | `instructions` (sticky) |
| **Capabilities** | *What* can it touch? | write | filesystem level+globs, bash, **model selection** |
| **Links** | *Who* can it talk to? | write | graph edges → `ask_agent` neighbor list |
| **Agent** | The live *work* | observe+interject | AG-UI stream (text/thinking/tools) |
| **Stats** | *How* is it doing? | observe | LM Studio REST + Pydantic AI usage |

Model selection lives in **Capabilities** ("what brain is it running on" is a capability), keeping Stats purely observational.

### Agent-to-Agent Communication (the crux)

**Delegation tool, structured envelope, free-form body, in-process.** Each agent gets an `ask_agent(target_id, question)` tool (Pydantic AI's agent-delegation pattern: an agent calls another from within a tool, with usage passed via `ctx.usage` and `UsageLimits` bounding runaway loops). The target runs with its own persona/capabilities, answers concisely, returns — so the asker's context isn't polluted by the target's research. This is exactly your React-expert/Node-expert example.

- **Connection awareness:** build each agent's `instructions` dynamically (`@agent.instructions`) to include its live graph neighbors: *"You can consult: `react_expert` — React/JSX questions; `node_expert` — backend questions."* Re-evaluated each run, so it stays current as you edit the graph.
- **Monitoring:** every `ask_agent` call + result is a tool event — streamed over AG-UI/SSE and logged to SQLite, so you watch inter-agent conversations live and review them later. Animate the active edge on the canvas.

### Tasks & Progress Tracking (how work enters and completes)

The key insight: **you are the orchestrator at design time.** Most multi-agent guides assume a runtime orchestrator that *invents* the team per request (Anthropic's orchestrator-workers). You've already drawn the org chart — who delegates to whom, who can touch what — so the runtime doesn't need a discovery engine. It just needs a **Task object that flows to an entry-point agent and along the links you drew.** This is why no heavyweight orchestration engine (LangGraph state machine) is needed.

Current practice (Claude Code native Tasks, Roo Code, Cline, Codex, the Claude Agent SDK) converges hard on one primitive: a **todo list the agent maintains as a tool call** — items with `pending`/`in_progress`/`completed` status, updated live. So progress tracking is mostly a rendering+logging problem, not an engine. Two enforcement rules are worth stealing: require a todo list before complex/delegated work, and **don't allow a task to complete while todos remain pending**.

**Tasks are first-class objects (fourth table, keyed by `session_id`):**

```
tasks
├─ id, session_id
├─ title / prompt           — what you asked for
├─ assigned_agent_id        — entry-point agent (defaults to the team's lead node)
├─ status                   — queued → running → blocked → needs_review → done | failed | cancelled
├─ completion_signal        — self_reported | reviewer:<agent_id> | check:<command>   ← per-task choice
├─ todos                    — live checklist [{content, status}]
├─ parent_task_id           — set when a delegation spawns a sub-task (the task tree)
├─ delegation_chain         — agents visited, for cycle detection
├─ result / summary
└─ created_at / updated_at
```

**The status lifecycle answers "when do we move on":**

```
queued ──► running ──► needs_review ──► done
   ▲          │              │
   │          ▼              ▼ (signal fails)
   │       blocked        needs_revision ──► running
   │   (awaiting input/                       (critique injected)
   │    delegated sub-task)
   └──────────────────────── failed / cancelled / cap-hit
```

- **queued → running:** the agent begins work on the task. (Whether this task's underlying *model calls* run concurrently with other agents' calls or are serialized is decided independently by the LLM execution gateway — see below. The task system marks a task `running` regardless; serialization happens beneath it.)
- **running → needs_review:** the agent emptied its todo list and believes it's done. It does **not** jump straight to `done` — the **completion_signal** gates it:
  - `self_reported` → auto-passes to `done` (you trusted the agent; no gate).
  - `reviewer:<agent_id>` → a reviewer agent in the graph receives the result and approves (→ `done`) or rejects with feedback (→ `needs_revision` → `running` with the critique injected). This *is* the evaluator-optimizer pattern.
  - `check:<command>` → the backend runs a deterministic gate in the repo (`pytest`, `npm test`, build/lint); exit 0 → `done`, nonzero → `needs_revision` with output as feedback. This *is* Anthropic's "gate" — no LLM judgment.
- **blocked:** waiting on a delegated sub-task or human input; surfaces on the board so you see *why* nothing's moving.

The per-task signal is the elegant payoff: the **same** machine serves a throwaway "rename this variable" (self-reported), an "implement auth, tests must pass" (`check: pytest`), and a "refactor this, senior agent reviews" (reviewer gate) — you pick the rigor at task-creation time, the machinery underneath is identical.

**Two code-level safety rails (the inoculation against the failure modes that sink ~40% of multi-agent pilots):**
1. **Hard caps per task:** max turns and max delegation depth (≤2–3 levels). Hitting a cap moves the task to `blocked` (your attention), never an infinite loop — kills the A→B→C→A handoff loop and endless replanning.
2. **Cycle guard on delegation:** track `delegation_chain`; refuse a delegation that would revisit an agent already in the current chain. (Cheap because the graph makes cycles detectable.)

Also adopt the **3-task rule** in the lead agent's instructions: don't create tracking overhead for trivial work (<3 steps), just do it.

**Where it surfaces in the UI:**
- **Per-agent (Agent tab):** the live todo checklist for that agent's current task, backed by the task's `todos`.
- **Session-level Task board (new view, not a sidebar tab):** a Kanban-style board (queued / running / blocked / needs_review / done) across the whole session, sub-tasks nested under their parent. This is the team-level "track the process" view — it belongs to the session, not one agent, so it lives alongside the canvas (toggle canvas ⇄ board, or a bottom panel), keeping the agent sidebar at five tabs.
- **Creating a task:** prompt + which agent (defaults to lead) + completion signal (self / reviewer / check). That's the whole intake.

### Sticky Persona / Compaction

- **`instructions` (not `system_prompt`):** always re-inserted on every model request and **not** carried in/lost to truncated message history — Pydantic AI's docs recommend `instructions` for exactly this. This *is* your sticky context.
- **`history_processors` for compaction:** a callable that takes the message list and returns a modified one — slice recent, or summarize-oldest-N-keep-rest (both are documented patterns). Because the persona lives in `instructions`, compaction never touches it. Read `ctx.usage.total_tokens` to trigger compaction only near the limit. Keep tool-call/tool-return pairs together when slicing.

### Streaming Thinking + Tool Use

- **Backend:** `agent.run_stream_events()` / `agent.iter()` for granular events (thinking deltas, tool-call start/result, text deltas), or `agent.to_ag_ui()` to emit AG-UI directly.
- **Transport:** SSE (FastAPI native). Unidirectional server→client, simpler than WebSockets. A "stop"/"interject" is a separate request against the agent's background task.
- **Frontend:** render streaming text, a collapsible thinking panel, and tool-call cards from the AG-UI event stream.

### Long-Lived Agents

- Each agent runs as a **background `asyncio` task** with a lifecycle status surfaced on its canvas node and Stats tab. The Agent tab subscribes to its event stream and can inject new user messages mid-run. This is purely about *agents being durable workers* — orthogonal to both the task system and the execution gateway.

### LLM Execution Gateway (low-spec / single-model mode)

A **separate system from the task system**, living at a different layer and answering a different question. The task system decides *what work exists and its state*; the gateway decides *how model calls dispatch against finite compute*. They are not the same mechanism — keep them apart.

- **What it is:** a single chokepoint that *every* model call funnels through — agent turns, `ask_agent` delegations, reviewer-gate evaluations, compaction summarizations. All LLM traffic, not just "tasks."
- **Parallel mode (default, capable machines):** the gateway is a pass-through; agents' model calls run concurrently via `asyncio.gather`.
- **Serial mode (toggle, low-spec machines with one model loaded that can't do parallel LLM calls):** the gateway holds one `asyncio.Semaphore(1)` (or a small FIFO queue) so exactly one model call is in flight at a time; the rest await their slot. Toggled **per session**.
- **Why a gateway, not a task-level queue:** a task-level queue would never see a reviewer's critique call, a compaction summary, or an `ask_agent` to an expert — none of those are "tasks," yet on a single-LLM machine they still must not run concurrently with other model calls. A chokepoint at the *model-call layer* serializes everything completely and automatically.
- **Independence from task state:** a task can be `running` (work logically in progress) while its current model call sits *waiting for a slot* in the gateway. Those are two different states about two different things — proof the systems are distinct. No Redis/Celery; in-process `asyncio` is right for local single-user. Surface "waiting for model slot" via the SSE stream so the UI can show why an agent is momentarily idle.

### Testing & Code Quality (front of mind)

Tests are a design constraint here, not an afterthought — and the goal is **tests that prove behavior, never change-detectors.** The litmus test for whether a test earns its place: *if it fails, does that mean something is broken, or merely changed?* Only the former is allowed. No tests of constants (`assert MAX_DEPTH == 3`), trivial getters, or framework behavior.

**The enabling technique — a scripted fake model.** Pydantic AI ships `TestModel` (auto-calls tools with no LLM) and `FunctionModel` (you script exactly what the "model" does each turn: "turn 1 → call `edit_file` with these args; turn 2 → emit this text"). This is the braindead abstraction that makes real end-to-end tests possible with **zero tokens, full determinism, millisecond runtime.** It is the single most important testing seam, and it only works if the model is **injected** into each agent (never constructed inside it) — so dependency-inject the model everywhere, which also keeps the code clean.

**Three tiers, weighted toward function + e2e (per your preference):**
1. **Function tests (the load-bearing layer):** exercise real behavior against a real temp-dir repo — `edit_file` with a stale `lines_hash` is rejected; a write outside `write_paths` raises; a read-only agent's toolset contains no write tool; the task state machine transitions `needs_review → needs_revision` when a `check:` gate fails; a 4-deep delegation hits the cap and lands in `blocked`; the cycle guard refuses a revisiting delegation. The sandbox path checks, the anchor/line logic, and the state transitions are **pure functions** — trivially testable, no mocks of the thing under test.
2. **End-to-end tests with a scripted model (the spine):** drive a whole session with `FunctionModel` — task enters → agent decomposes (todos) → calls `edit_file` → delegates via `ask_agent` → reviewer gate runs → task reaches `done` — and assert on **real filesystem changes and real task-state/event transitions**. This is the test that proves the system actually works end-to-end, and it's the kind your instinct rightly favors over a pile of tiny units.
3. **Live-LLM smoke tests (thin, off by default):** a handful gated behind an env flag, run nightly/manually, hitting a real model to catch "does the prompt actually make the model use the tool." Never in the fast suite.

**Code-quality rules that make the above cheap and keep files readable:**
- **Small, single-purpose modules** (the structure below targets one concern per file; a soft ~300-line ceiling — if a file grows past it, it's doing too much and should split).
- **Pure where possible:** path/glob checks, hash/anchor logic, state transitions, neighbor-list building take inputs and return outputs with no I/O — the most valuable code to test and the easiest to read.
- **Dependency injection over globals** (already required by the multi-session design): inject the model, the repo root, the clock, the event bus. This is what lets `FunctionModel`, temp dirs, and fake clocks drop in.
- **Side effects at the edges:** filesystem, model calls, and SSE live in thin adapters; the logic in between stays pure and synchronous where it can.

### Suggested Project Structure

```
agentteam/
  backend/
    main.py          # FastAPI app, uvicorn entry; boots SessionManager
    db.py            # SQLite: teams, sessions, agent_state, tasks tables
    teams.py         # TeamStore — team-definition CRUD (templates, repo-agnostic)
    sessions.py      # SessionManager + Session (owns registry, lock, gateway, bus, repo)
    graph.py         # node/edge model within a team definition
    agents.py        # build Pydantic AI Agent per node (within a session)
    capabilities.py  # capability profile → dev_toolset (sandbox + globs)
    persona.py       # instructions + history_processors (compaction)
    a2a.py           # ask_agent tool + neighbor-list injection (session-scoped)
    tools.py         # dev toolset: read(range)/write/edit(line-range+hash)/grep/find/bash — pure where possible
    tasks.py         # Task object, status lifecycle, completion signals, caps + cycle guard
    todos.py         # write_todos tool + checklist rendering/logging
    runtime.py       # RunningAgent: long-lived task + lifecycle (per session)
    gateway.py       # LLM execution gateway: parallel pass-through | serial Semaphore(1), per session
    locks.py         # per-session filesystem write-lock
    stats.py         # LM Studio REST + usage aggregation
    streaming.py     # AG-UI / SSE endpoints, events tagged with session_id
    models.py        # per-agent model resolution (hosted + local); injected, never built in-place
    db.sqlite
  tests/
    test_tools.py        # function tests: path/glob enforcement, edit stale-hash reject, line-range edits
    test_tasks.py        # function tests: state machine, completion gates, caps → blocked, cycle guard
    test_capabilities.py # function tests: profile → toolset (read-only has no write tool)
    test_e2e_session.py  # end-to-end with FunctionModel: task → edit → delegate → review → done
    conftest.py          # temp-repo fixture, FunctionModel scripts, fake clock/bus
  frontend/
    src/
      TeamEditor.tsx       # edit a team definition (template)
      Canvas.tsx           # React Flow + floating "+"  (used by editor & session view)
      AgentNode.tsx        # custom node w/ live status
      SessionView.tsx      # running control room for one session
      TaskBoard.tsx        # session-level Kanban board of tasks (canvas ⇄ board toggle)
      NewTaskDialog.tsx    # prompt + agent + completion signal
      SessionSwitcher.tsx  # list/switch/launch sessions (later phase)
      Sidebar/{Persona,Capabilities,Links,Agent,Stats}.tsx
      api.ts               # all calls carry session_id / team_id
    vite.config.ts
  README.md          # `uvicorn backend.main:app` + `npm run dev`
```

### Phased Build Plan

*Each phase ships its function tests alongside the code (the pure logic — sandbox, tools, state machine — is tested as it's written). The end-to-end `FunctionModel` test arrives with the task system in Phase 5, once there's a full flow worth driving.*

- **Phase 0 — Skeleton + seams (1 day):** FastAPI + Vite hello-world, both terminal-start. SQLite schema with all four tables (`teams`, `sessions`, `agent_state`, `tasks`) — every row keyed by `team_id`/`session_id`. `SessionManager` + `Session` wrapper exist and own the (currently single) registry/lock/gateway/bus. App auto-creates one team + one session on startup; the concept is invisible in the UI. **This phase is where the multi-repo future is bought.**
- **Phase 1 — Graph MVP (2–3 days):** React Flow canvas, floating "+", drag-to-connect edges, 5-tab sidebar shells. Persist the graph as the one team's definition.
- **Phase 2 — Single working agent + tools (3–4 days):** one Pydantic AI agent → model dropdown (hosted + LM Studio), **model injected** for testability. Persona tab → `instructions`. Capabilities tab → generate the dev toolset (read-with-range, write, **line-range `edit` with hash check**, grep/find, bash) with sandbox + globs, bound to the session's repo. `write_todos` wired in; Agent tab streams text/thinking/tools + renders the live checklist via AG-UI/SSE. Stats tab from LM Studio REST. **Milestone: an agent edits the session's repo, uninterrupted, inside its box, with a visible checklist.** Ships `test_tools.py` + `test_capabilities.py`.
- **Phase 3 — Long-lived tasks (2–3 days):** agents as background tasks with lifecycle, owned by the session's registry; interject mid-run; status on canvas nodes.
- **Phase 4 — Agent-to-agent (3–4 days):** `ask_agent` + dynamic neighbor injection; Links tab edits edge labels; stream/log/visualize inter-agent conversations (all session-scoped).
- **Phase 5 — Task system (4–5 days):** the `tasks` lifecycle (queued→running→blocked→needs_review→done), `NewTaskDialog` (prompt + agent + completion signal), the session-level `TaskBoard`, the three completion signals (self / reviewer agent / `check:` command), delegation tree via `parent_task_id`, and the two safety rails (turn/depth caps → `blocked`; cycle guard). **This is the "give the team a task and track it to completion" milestone.** Ships `test_tasks.py` + the `test_e2e_session.py` `FunctionModel` end-to-end test (task → edit → delegate → review → done, asserting real repo + state changes, no LLM).
- **Phase 6 — Compaction + execution gateway (2 days):** `history_processors` compaction; the LLM execution gateway as the chokepoint all model calls route through, with the per-session parallel/serial toggle; per-session write-lock. (Gateway is independent of the Phase 5 task system.)
- **Phase 7 — Team library (2 days):** UI to save the current graph as a named team, list teams, load one into the editor. Pure CRUD on the `teams` table — the runtime already supports it. Enforce "session pins its definition at launch."
- **Phase 8 — Multi-session (3–4 days):** launch a team against a chosen repo (repo-picker dialog + same-repo warning), session switcher, concurrent session views. The runtime already supports N sessions; this is frontend + the launch flow.
- **Phase 9 — Snapshot/resume + polish (ongoing):** persist and rehydrate `agent_state` + in-flight `tasks` for pause/resume; cost estimates, persisted work logs, export/import teams, per-command bash allowlist, optional Docker sandbox executor.

### Reference Open-Source Projects

- **Open Agent Builder (Firecrawl):** Next.js + React Flow + LangGraph visual builder with SSE streaming — closest reference for canvas + execution streaming.
- **Open Gumloop (Composio):** React Flow + LangGraph.js node platform — reference for node/edge JSON model.
- **Flock:** LangGraph + FastAPI + Next.js + React Flow — reference for the exact backend/frontend split here.
- **Pydantic AI AG-UI examples + CopilotKit AG-UI Dojo:** reference for streaming chat, tool rendering, the Pydantic-AI→AG-UI bridge.
- **`pydantic-ai-backend` (vstorm-co):** Console Toolset (ls/read/write/edit/grep/execute) — drop-in alternative to the hand-rolled dev toolset.
- **oh-my-pi (`can1357/oh-my-pi`) and Pi (`badlogic/pi-mono`, Mario Zechner):** the minimal read/write/edit/bash core and the **hashline edit format** — the reference for context-efficient, reliable edits. Study `edit` with LINE#ID anchors before building the v1 line-range editor.

## Recommendations

1. **Adopt the team-definition / session-instance split now as the spine of the data model and runtime.** Four tables (`teams`/`sessions`/`agent_state`/`tasks`), one process holding a `dict[session_id, Session]`, and per-session ownership of registry/lock/gateway/bus. Costs a column and a wrapper today; saves a core rewrite later.
2. **Build per-agent toolsets from the capability profile** — read-only agents never get write tools; no-bash agents never get bash. Enforcement lives in the tool layer, not the persona prose.
3. **Keep the graph as plain data** (adjacency list in SQLite), agents as independent Pydantic AI `Agent`s, edges as delegation permissions. Avoids LangGraph state-machine boilerplate while giving a true agent graph.
4. **Treat agents as long-lived background tasks**, observed and interjectable — this is what makes "uninterrupted work" real, and it's the biggest change from a naive chat app.
5. **Use `instructions` for persona, `history_processors` for compaction, `ask_agent` for delegation, `write_todos` for progress, AG-UI for streaming** — each maps to a first-class or industry-standard pattern, minimizing custom code.
6. **Tasks are first-class with per-task completion signals and code-level caps.** Route work to an entry-point agent, track via the status lifecycle and a session-level board, gate completion (self / reviewer / check) per task, and enforce turn/depth caps + cycle guards in code — agent-driven flexibility with structural loop-proofing. Start agent-driven; the gates and caps are the only structure you add up front.
7. **Model is per-agent config** — mix hosted (Claude/GPT, for tool-heavy working agents) and local (LM Studio, for cheap expert agents) freely.
8. **Keep the task system and the LLM execution gateway separate.** Tasks = *what work exists and its state* (hardware-independent). Gateway = *how model calls dispatch against compute* (serial `asyncio.Semaphore(1)` per session on low-spec machines, else pass-through). Write safety = one `asyncio.Lock` per session. No external infra; skip auth (local only).
9. **The edit tool is the highest-leverage piece of the harness** (a ~10× success swing comes from format alone). Ship a line-range `edit` with a content-hash staleness check in v1 — context-efficient and a free concurrent-write guard — and keep the rest of the tool core Pi-minimal (read-with-range, write, bash, grep/find). Graduate to full hashline anchors if local-model edit reliability disappoints.
10. **Make the model an injected dependency and test behavior, not constants.** `FunctionModel`/`TestModel` give real, deterministic, token-free end-to-end tests; weight toward function + e2e tests of actual behavior over tiny units, keep modules small and logic pure, and never write a test whose failure means "changed" rather than "broken."

**Thresholds that change these recommendations:**
- Need **true non-interference / untrusted tasks** → swap the bash executor to Docker-per-agent (repo mounted); tool interface unchanged.
- Need **complex cyclic control flow** between agents (beyond ask/answer) → move orchestration to LangGraph, keep React Flow + AG-UI.
- Outgrow **single-user/local** → switch local inference to vLLM (PagedAttention/continuous batching, which removes the need for serial mode entirely) and move per-session state to a shared store.
- Want **TypeScript end-to-end** → Mastra + Hono (lose Pydantic AI ergonomics).

## Caveats
- **Local-model tool-calling is the main risk.** Dev-working agents lean on reliable function-calling; test `run_bash`/`edit_file`/`ask_agent` early with your specific LM Studio model, and expect to run the *working* agents on hosted endpoints while local models handle cheap lookups.
- **Path-check sandbox is not escape-proof** (accepted for v1). `run_bash` with absolute paths/`cd ..` can leave the repo. Graduate to Docker if needed.
- **Concurrent writes** are still possible if you configure two write-capable agents with overlapping write globs in parallel mode. The write-lock prevents byte-level corruption and the `edit` hash-check rejects edits to a stale view, but neither prevents *logical* conflicts (two valid-but-incompatible edits). Scoping write paths so they don't overlap avoids this by construction.
- **Edit-tool format is load-bearing and model-sensitive.** A line-range/hash edit is far better than `str_replace` for most models, but no format is universal — validate your chosen `edit` against your actual local models early (the same models that struggle with tool-calling also struggle most with edits). The hash-check rejection path *will* fire when an agent edits without re-reading; make the error message instruct a re-read so the model self-corrects rather than loops.
- **`FunctionModel` tests verify your machinery, not the prompts.** A green e2e suite proves the task flow, sandbox, gates, and state machine are wired correctly — it says nothing about whether a real model will choose the right tool. That's what the thin live-LLM smoke tier is for; don't let deterministic coverage create false confidence about real-model behavior.
- **Pydantic AI multi-agent primitives are lighter than LangGraph's** (it orchestrates via `pydantic-graph` FSMs); fine for ask/answer delegation, validate early if you need complex routing.
- **LM Studio context quirk:** `loaded_context_length` reflects loaded config (often a small default), not the model max — read the loaded value in Stats and raise it in LM Studio to avoid silent truncation.
- **AG-UI is young** and CopilotKit-driven; pin versions. The agent-framework space churns fast; budget for periodic upgrades.
- **Concurrent sessions share host resources** even though they're logically isolated: many sessions hammering one local LM Studio instance will queue at the model, and many hosted-API sessions share your rate limits and cost. The gateway's per-session serial mode and per-agent model choice are the release valves. Logical isolation (separate locks/registries/buses) is not resource isolation.
- **Self-reported completion is the weakest signal** — local/cheaper models in particular tend to declare victory early. For anything load-bearing, prefer a `check:` gate (deterministic, free of LLM judgment) or a reviewer agent. Reserve `self_reported` for low-stakes tasks.
- **Reviewer loops can ping-pong.** An evaluator-optimizer pair can bounce needs_revision↔running indefinitely if the bar is fuzzy. The turn cap catches it (→ `blocked`), but give reviewers concrete, checkable criteria and cap revision rounds explicitly.
- **The todo list is the agent's plan, not ground truth.** It reflects what the agent *intends*/*believes done*, which can drift from reality (marked complete but tests fail). The `check:` gate is what reconciles intent with reality; don't treat an empty checklist alone as "done" for important tasks.
