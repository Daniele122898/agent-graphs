# OpenCode as the agent harness — feasibility research

*Researched 2026-06-12 against the locally installed `opencode 1.16.2` (live
OpenAPI probe) + the official docs + the Python SDK checkout. No code changes
made; this is the reference for a possible transition.*

**Verdict: feasible, and a good fit.** OpenCode would replace our *harness
layer* (`backend/agents/`, `backend/providers/`, `gateway`, most of
`backend/runtime/`) — not the product. The team graph, task system, completion
gates, delegation guards, and the control room stay ours. Several things we
hand-built are native in OpenCode (questions, todos, permissions, compaction,
context view). The decisive unknown is whether our small local models hold up
inside OpenCode's loop — see the spike plan at the bottom.

---

## 1. Ground-truth sources

- **Live server probe**: `opencode serve --port 4096` against a temp dir, then
  `curl http://127.0.0.1:4096/doc` → full OpenAPI 3.1 spec (~133 paths).
  Re-probe any time the same way; a copy of the spec from this session is NOT
  committed (it's version-specific) — regenerate against whatever version is
  installed.
- **Python SDK checkout**: `~/code/opencode-sdk-python` — version
  `0.1.0-alpha.36` (2025-08-27). **PyPI's latest is the same version → the
  Python SDK is abandoned/stale (~10 months behind the server).** It still
  models the "modes" era: no question endpoints, no permission reply, no
  per-session directory, no `prompt_async`. **Do not build on it.** The
  maintained SDKs are TS (`@opencode-ai/sdk`) and Go. Integration path for us:
  plain HTTP via httpx (optionally a client generated from the live `/doc`).
- **Docs**: [Server](https://opencode.ai/docs/server/) ·
  [Agents](https://opencode.ai/docs/agents/) ·
  [Custom Tools](https://opencode.ai/docs/custom-tools/) ·
  [MCP](https://opencode.ai/docs/mcp-servers/) ·
  [Permissions](https://opencode.ai/docs/permissions/) ·
  [Plugins](https://opencode.ai/docs/plugins/) ·
  [Providers](https://opencode.ai/docs/providers/) ·
  [Models](https://opencode.ai/docs/models/) ·
  [Rules](https://opencode.ai/docs/rules/) ·
  [Config](https://opencode.ai/docs/config/) ·
  [SDK](https://opencode.ai/docs/sdk/) ·
  [CLI](https://opencode.ai/docs/cli/)
- Repo: github.com/anomalyco/opencode (renamed from sst/opencode — old links
  redirect; many third-party writeups predate the rename).

## 2. What the current server exposes (verified live on 1.16.2)

Highlights from the `/doc` probe relevant to us:

- **Sessions**: `POST /session` accepts `{parentID, title, agent, model:
  {providerID, id, variant}, metadata, permission: PermissionRuleset,
  workspaceID}` — i.e. per-session model AND per-session permission rules at
  create time. `GET/PATCH/DELETE /session/{id}`, `/children`, `/fork`,
  `/abort`, `/diff`, `/shell`, `/command`.
- **Prompting**: `POST /api/session/{id}/prompt` body
  `{prompt: {text, files, agents, references}, delivery: "steer"|"queue",
  resume, id}` plus `POST /session/{id}/prompt_async` and
  `POST /api/session/{id}/wait`. Docs also show per-call `model`, `agent`,
  `system` (full system-prompt override), `tools` (per-call toggles) and
  `format: {type: "json_schema", schema, retryCount}` → **structured output
  per prompt** (our reviewer gate, natively).
- **Questions (= our ask_user)**: native. `GET /question`,
  `POST /question/{requestID}/reply` with
  `{answers: [[selected labels...]]}`, `/reject`; per-session variants too.
  `QuestionInfo = {question, header, options, multiple, custom}` — multiple
  choice + multi-select + free-form. Events: `question.asked/replied/rejected`.
- **Permissions**: `GET /permission`, `POST /permission/{requestID}/reply`,
  per-session `POST /session/{id}/permissions/{permissionID}`
  `{response, remember?}`. `PermissionRule = {permission, pattern, action}`.
  Permission types include **`read` (path globs!)**, `edit`, `bash` (command
  patterns), `glob`, `grep`, `list`, `task` (subagent name), `webfetch`,
  `external_directory`, `doom_loop`. "ask" decisions stream over SSE → our
  backend can be the policy engine and auto-allow/deny from Python.
- **Todos**: native — `GET /session/{id}/todo`, `todo.updated` events.
- **History**: `/compact`, `/summarize`, `/revert`, `/unrevert`,
  `DELETE /session/{id}/message/{messageID}`,
  `PATCH/DELETE .../part/{partID}`, `opencode export/import` (full session
  JSON round-trip). **`GET /api/session/{id}/context` returns the actual
  message array sent to the model** — our Agent-tab "what does the model see"
  view, native.
- **Agents**: `GET /agent` → `{name, description, mode:
  primary|subagent|all, model, variant, prompt, temperature, topP, steps,
  permission, options, ...}`.
- **Events**: one global SSE bus (`/event`, newer docs `/global/event`), ~90
  event types incl. message/part updates, **text + reasoning deltas**, tool
  call/success/fail, `session.idle` (the canonical "run finished"),
  `session.error/status/compacted`, question/permission asked/replied,
  `todo.updated`, `file.edited`. (A `SessionNext*` event family signals an
  in-flight streaming redesign — churn risk.)
- **Multi-project**: one server hosts many directories — `?directory=` query
  scopes sessions; `/project`, `/experimental/worktree`, `/experimental/workspace`.
  Newer/less-hardened path (see issue [#12271](https://github.com/anomalyco/opencode/issues/12271),
  multi-root requests [#19515](https://github.com/anomalyco/opencode/issues/19515)).
- **Usage/cost**: AssistantMessage carries `tokens {input, output, reasoning,
  cache}` + `cost` per message; `opencode stats`.
- Misc: `/pty` (real terminals), `opencode acp` (Agent Client Protocol — an
  alternative integration surface we likely don't need), basic auth via
  `OPENCODE_SERVER_PASSWORD`.

## 3. Feature mapping (ours → OpenCode)

| Ours today | OpenCode equivalent | Fit |
|---|---|---|
| RunningAgent (long-lived worker, persistent history) | One session per team-agent; `prompt_async` + `session.idle`; `delivery: queue` = our interjection, `steer` is new | ✓ |
| Persona assembly (sticky instructions) | Per-agent `prompt` — **REPLACES the entire built-in system prompt** (confirmed; no append mode). `{file:...}` refs supported | ✓ (good for small models) | EDIT: we should be careful with this and research this more and the opencode system prompt has had a lot of thought put into it and may be required!
| Capabilities (read/write globs, bash on/off) | Per-agent/per-session permission rulesets (incl. `read` path globs, bash command patterns) + per-agent `tools: {name: bool}` | ✓ (stronger than ours) |
| ask_agent + neighbor/cycle/depth guards | **Custom TS tool** calling back into our backend (see §4) — guards stay in Python; target runs on its persistent session | ✓ via callback |
| ask_user / QuestionBoard / answer UI | Native question system (tool + endpoints + events) | ✓ near 1:1 |
| write_todos | Native todo tool + endpoint + events | ✓ |
| Edit-token edit tool (hash staleness) | Native edit + diff permissions + snapshots/revert. No optimistic-concurrency hash | ~ (acceptable loss) |
| Compaction / Clear / Summarize | Native compact/summarize; message DELETE endpoints; clear ≈ fork or new session | ✓ |
| Agent tab transcript + system context | `GET /session/{id}/message` + `GET /api/session/{id}/context`; SSE deltas (finer than ours) | ✓ |
| Gateway serial mode (one model call at a time) | **No equivalent.** Approximate: backend queues prompt submissions per repo; LM Studio queues server-side anyway | ✗ gap (coarse workaround) |
| Providers: LM Studio / DeepSeek + thinking effort | LM Studio via `npm: "@ai-sdk/openai-compatible"` + `baseURL` (documented example); DeepSeek built-in (models.dev). Per-model `options` (incl. `reasoningEffort`), model **variants**; model per agent and per prompt call | ✓ |
| AGENTS.md/CLAUDE.md context loading (just built) | Native: AGENTS.md upward traversal, CLAUDE.md fallback (`OPENCODE_DISABLE_CLAUDE_CODE=1` to disable), `instructions` globs, lazy `@file` refs | ✓ |
| UsageTally | Native per-message tokens + cost | ✓ |
| Anti-stall (todos continuation nudge), delegation busy-timeout | Rebuild on top of `session.idle` + todo endpoint; `doom_loop` permission helps | ~ (re-implement, cheap) |
| Task system, completion gates, reviewer | **Stays ours.** Reviewer gets easier: per-prompt `format: json_schema` structured output | ✓ ours |
| Team graph, canvas, control room | **Stays ours** — frontend consumes their SSE through an adapter in our backend (or directly) | ✓ ours |

Native subagents (the `task` tool + `permission.task` name rules) could model
graph edges, but they spawn **fresh child sessions** each time — they are not
our persistent peers. Keep delegation as our custom tool.

## 4. The custom-logic mechanism (the key enabler)

Custom tools are TS/JS files in `.opencode/tools/` (or global
`~/.config/opencode/tools/`), run under Bun:

```ts
import { tool } from "@opencode-ai/plugin"
export default tool({
  description: "Consult a teammate...",
  args: { target_id: tool.schema.string(), question: tool.schema.string() },
  async execute(args, ctx) {
    // ctx = { sessionID, messageID, agent, abort, directory, worktree }
    const r = await fetch("http://127.0.0.1:8000/internal/ask_agent", {
      method: "POST",
      body: JSON.stringify({ ...args, sessionID: ctx.sessionID, agent: ctx.agent }),
    })
    return await r.text()
  },
})
```

- `execute` **knows the calling session + agent** and may block indefinitely
  (no documented timeout; honor `ctx.abort`) — same parking pattern as our
  ask_user today.
- Our backend endpoint enforces neighbor/cycle/depth guards and drives the
  target agent's persistent session via the server API, then returns the answer.
- Custom tools can **shadow built-ins by name**; built-ins can be disabled
  per agent (`"tools": {"bash": false, ...}`).
- Plugins (same dirs / npm) add hooks: `tool.execute.before/after` (fires for
  MCP tools too, carries sessionID), `permission.ask` (auto allow/deny),
  `chat.params`, compaction-prompt override, full event hook, and can add
  tools. **No hook mutates the system prompt** — agent `prompt` / per-call
  `system` are the levers.
- MCP also works (`mcp` config key, tools named `server_tool`, per-agent
  enable via tool globs) but **MCP tool calls do NOT carry session identity**
  — for session-aware tools, prefer custom TS tools or plugin hooks.

## 5. Proposed architecture (if we do it)

- **One `opencode serve` process per agent-graphs session** (not the shared
  multi-directory mode — cleaner isolation, matches our per-session ownership;
  multi-directory has open bugs). Config injected via `OPENCODE_CONFIG_CONTENT`
  env at spawn — nothing written into the user's repo except
  `.opencode/tools/ask_agent.ts` (or that can live in the global config dir).
- **One OpenCode session per team agent**, created with the agent's
  model/prompt/permissions (generated from our AgentSpec + graph). Graph/spec
  edits → recreate or PATCH config.
- **Our FastAPI keeps**: teams/graph/storage, task lifecycle + gates,
  delegation guard endpoint, question/permission routing to our UI, usage
  aggregation. **Deleted**: `agents/` (factory, tools, persona, questions,
  todos, history), `providers/`, `gateway`, most of `runtime/`
  (workers/streaming). `wiring.py` becomes the OpenCode adapter.
- **Frontend**: `useEvents` consumes a translated event stream (our backend
  bridges their SSE → our event names), or eventually their events directly.
  Transcript = their message/part shapes.

## 6. Risks

1. **Small local models (the make-or-break)**: OpenCode's loop/prompts are
   tuned for frontier models. We can replace prompts and slim toolsets per
   agent, but qwen3.5-9B tool-calling reliability inside their loop is
   unproven. Also unknown: whether their LM Studio path avoids re-sending
   reasoning traces (our hard-won `send_back_thinking_parts=False` lesson).
2. **API churn**: Python SDK abandoned; events endpoint moved; `SessionNext*`
   migration in flight; multi-directory bugs. Pin the opencode version;
   generate clients from the live `/doc`.
3. **No serial gateway** — only coarse queueing of prompt submissions.
4. **Bun dependency** for custom tools/plugins (opencode bundles it; still a
   new moving part).
5. Loss of our edit-token optimistic concurrency for same-file races between
   agents (their snapshots/diffs mitigate).

## 7. Recommended next step: a one-day spike (no integration yet)

Scratch script (outside the app) that:
1. Spawns `opencode serve` against a throwaway repo with
   `OPENCODE_CONFIG_CONTENT`: LM Studio provider (`@ai-sdk/openai-compatible`,
   baseURL `http://127.0.0.1:1234/v1`), one agent with a minimal replaced
   prompt + slimmed tools (`task=false`, `webfetch=false`, ...), permission
   ruleset mirroring one of our capability profiles.
2. Drives one coding task on `lmstudio/qwen/qwen3.5-9b` via `prompt_async`,
   tailing `/event` (does it tool-call reliably? does it stall? does
   reasoning get echoed back into context?).
3. Adds `.opencode/tools/ask_agent.ts` calling a 20-line local HTTP stub;
   verifies the tool fires with `ctx.sessionID/agent` and a blocked `execute`
   parks the run cleanly.
4. Triggers the native question tool and answers it via
   `POST /question/{id}/reply`.

Pass → plan the adapter swap behind our existing HTTP API (frontend mostly
unchanged). Fail on (2) → keep our pydantic-ai harness and steal ideas
instead (permission rulesets, steer/queue delivery, json_schema gates).

## 8. Reference shapes (copied from the live 1.16.2 spec)

```jsonc
// POST /session
{ "parentID": "ses…", "title": "…", "agent": "…",
  "model": {"providerID": "…", "id": "…", "variant": "…"},
  "permission": [ {"permission": "edit", "pattern": "docs/*", "action": "allow"} ],
  "workspaceID": "wrk…" }

// POST /api/session/{id}/prompt
{ "prompt": {"text": "…", "files": [], "agents": [], "references": []},
  "delivery": "steer" | "queue", "resume": false }

// POST /question/{requestID}/reply
{ "answers": [ ["selected label", …], … ] }   // per question, in order

// QuestionInfo
{ "question": "…", "header": "≤30 chars", "options": […],
  "multiple": false, "custom": true }

// AssistantMessage (usage)
{ "tokens": {"input": n, "output": n, "reasoning": n,
             "cache": {"read": n, "write": n}}, "cost": n, … }
```

---

## 9. Smoke test results — 2026-06-13 (branch `opencode-harness`)

A live throwaway spike (under `/tmp/oc_smoke`, all artifacts disposable) ran the
spike plan from §7 against `opencode 1.16.2` + LM Studio `qwen/qwen3.5-9b`. The
setup: an `opencode.json` with the LM Studio openai-compatible provider + one
`smoke` agent (replaced prompt, `task`/`webfetch`/`websearch` disabled), a
custom `.opencode/tool/ask_agent.ts` that `fetch()`es a local Python stub, and
the stub logging every call. **Verdict: the core feasibility is PROVEN.** Each
finding below was observed directly, not inferred.

### PASS — the make-or-break results
- **Config + provider wiring loads clean.** `GET /config` showed the `lmstudio`
  provider and `smoke` agent; serve log: `pkg=@ai-sdk/openai-compatible using
  bundled provider` — the openai-compatible npm provider is **bundled**, no
  `npm install` needed.
- **The small local model tool-calls reliably inside opencode's loop.** Prompt
  "create hello.txt with banana" → the model called the native `write` tool,
  opencode executed it, the file appeared. (Sanity-checked the model in
  isolation too: a direct LM Studio `/v1/chat/completions` with a tool returned
  a clean `tool_calls` response.) This was the #1 risk in §6 — **retired.**
- **Custom-tool callback (the `ask_agent` enabler) works end to end WITH
  identity.** The model called our `ask_agent.ts`; it called back into the
  Python stub; the stub received `{"target_id": "reviewer", "question": "...",
  "sessionID": "ses_...", "agent": "smoke"}` — i.e. the calling **session and
  agent identity are available to the tool** (from `ctx`), exactly what our
  neighbor/cycle/depth guards + persistent-target driving need. The model then
  relayed the stub's answer. Custom tool registered without `npm install`
  (serve log: `service=tool.registry status=completed ask_agent`).
- **Per-agent prompt replacement + tool disabling loads** (agent's `prompt` came
  back replaced; `task`/`webfetch`/`websearch` set false in config).

### How to actually drive a run (API gotchas — cost real time to find)
- **Use `POST /session/{id}/prompt_async` (fire-and-forget) or the synchronous
  `POST /session/{id}/message`.** Both reliably start a run on a fresh session.
  - `prompt_async` returns an **empty body** (don't JSON-parse it) and runs in
    the background; poll `GET /session/{id}/message` or the SSE bus for results.
    A trivial prompt produced assistant text in ~3s. **This is the production
    path** for our streaming control room.
  - `/session/{id}/message` blocks until the whole run finishes and returns the
    AssistantMessage (the maintained SDK's `chat`). Fine for scripts, wrong for
    a streaming UI.
  - Body shape that worked: `{"providerID":"lmstudio","modelID":"qwen/qwen3.5-9b",
    "agent":"smoke","parts":[{"type":"text","text":"..."}]}`.
- **DO NOT use `POST /api/session/{id}/prompt` to START a run.** It returns 200
  with `{admittedSeq, delivery:"steer"}` but on a *fresh* session it produced
  **zero messages / no run** — `delivery:"steer"` steers an *in-flight* run; it
  is not how you kick one off. This silently ate the first test (looked like a
  hung model; was actually nothing running).
- Session create that worked: `POST /session?directory=/tmp/oc_smoke`
  `{"title":"...","agent":"smoke"}` (the `?directory=` query scopes it).
- The SSE bus is at `GET /event` (server.connected + periodic
  `server.heartbeat`; real per-session events arrive there too once a run is
  actually running). `/experimental/tool` needs a `?provider=` query (returns a
  `Missing key at ["provider"]` query-rejection otherwise); the flat
  `GET /experimental/tool/ids` is the easy way to confirm a custom tool loaded.

### CAVEAT — native `question` tool (our `ask_user`) needs prompt work
- Prompted to ask the user a multiple-choice question via the native `question`
  tool, the 9B model **did not pick it reliably**: it ran `bash` (hunting for a
  config file), then mis-called `ask_agent`, then **ended its turn with a
  plain-text question** — the exact anti-pattern our current harness's prompt
  guidance ("NEVER end your turn with plain-text questions") exists to kill.
- So: the native question system (tool + `GET /question` +
  `POST /question/{id}/reply {answers:[[label]]}` + events) is present and the
  API is verified, but **reliable small-model adoption requires carrying over
  our prompt engineering** — not a free win, but a solved problem on our side.
  (Run-parking-on-a-question wasn't reached because the tool was never invoked;
  re-test once the agent prompt nudges the right tool.)

### Performance notes (weak laptop, qwen3.5-9b w/ reasoning)
- Cold model load ~10s (via `lms load`); **first model step ~38s**; warm steps
  ~8-9s. Tool tasks completed in 8-9s warm. Budget generous first-call timeouts.

### Net
The two hardest unknowns (small-model tool-calling in-loop; the custom-tool
delegation callback with identity) **both pass**. The remaining work is
integration plumbing (async-drive + SSE translation, carry our ask_user prompt
guidance, the serial-gateway gap from §6) — not a feasibility question. The
recommendation stands: this is a viable harness swap. Reusable spike artifacts
left in `/tmp/oc_smoke` (`opencode.json`, `.opencode/tool/ask_agent.ts`,
`stub.py`, `drive_sync.py`, `drive_question.py`) — `/tmp` is ephemeral, copy
them into the repo if continuing.

---

# PART B — Full integration plan (2026-06-13, branch `opencode-harness`)

Goal: run the product on EITHER our pydantic-ai harness ("native") OR an
OpenCode-backed harness, switchable per session, with 100% feature parity
(tasks, run/interject, stop, history/clear/summarize, ask_user, ask_agent
delegation+guards, lifecycle badges, todos, usage, SSE control room). The
abstraction is intentionally shallow: one `Harness` interface keyed by
`agent_id`; everything the product needs from "an agent" goes through it.

## B.1 Architecture / the abstraction

New package `backend/harness/`:
- `base.py` — `Harness` ABC + shared data shapes (`HistoryView`) + the shared
  delegation guard `check_delegation(graph, asker, target, chain)`.
- `native.py` — `NativeHarness`: thin wrapper over today's code
  (obtain_worker/RunningAgent, QuestionBoard, UsageTally, Delegator,
  agent_state, gateway, wiring helpers). ZERO behavior change.
- `opencode/` — the OpenCode-backed harness (server mgr, config gen, client,
  event translator, the harness itself).
- `__init__.py` — `make_harness(harness_id, ...) -> Harness` factory.

`Session` (runtime/sessions.py) gains `self.harness: Harness`, chosen at
creation. KEEP on Session as UNIVERSAL: id, team_id, repo_root, graph, status,
created_at, **bus** (the SSE sink both harnesses publish to), **registry** (the
lifecycle-badge map both harnesses update; native also stashes live workers in
it). MOVE behind the harness: gateway, write_lock, UsageTally, QuestionBoard
(native-only internals; OpenCode has its own).

### The `Harness` interface (keyed by agent_id; no RunningAgent leaks out)
```
id: ClassVar[str]                        # "native" | "opencode"
async start(session)                     # optional bring-up (opencode: spawn server)
async shutdown(session)                  # teardown (stop workers / kill server)
async submit(session, agent_id, prompt)              # run + interject (queued; streams to bus)
async run_to_completion(session, agent_id, prompt, *, usage=None,
                        delegation_chain=None, lock_timeout=None) -> str   # task + delegation
async stop(session, agent_id)
is_busy(session, agent_id) -> bool
async history(session, agent_id) -> HistoryView      # {instructions:[str], rows:[dict], message_count:int}
async clear_history(session, agent_id)
async summarize_history(session, agent_id) -> list[dict]   # rendered rows
list_questions(session) -> list[dict]
answer_question(session, question_id, answers) -> bool     # ValueError on count mismatch
usage(session, agent_id) -> dict
async delegate(session, asker_id, target_id, question, *, usage=None, chain=None) -> str
async run_reviewer(session, reviewer_id, task_prompt, result) -> ReviewVerdict
```
`HistoryView` rows reuse the EXACT shapes `history.render_messages` emits, so
the frontend renders both harnesses identically. `delegate` is concrete on the
base class (shared guards + bus a2a_message + waiting-on-agent + run_to_completion);
subclasses only differ in run_to_completion. The bus event names/shapes are the
fixed contract (user_message, agent_lifecycle, model_request, thinking, text,
tool_call, tool_result, todos, agent_done, agent_error, a2a_message,
user_question, user_question_done, task_status, model_wait).

### Selection
`config.yml` `harness: native` (default) + env `AGENT_GRAPHS_HARNESS`; plus a
per-session override in the launch request (`LaunchSessionRequest.harness`), so
you can run one native + one opencode session side by side. Persisted on the
sessions row (new nullable column, defaults native).

## B.2 OpenCode-backed harness design

- **Server lifecycle**: one `opencode serve` per agent-graphs session (clean
  isolation; matches per-session ownership). `OpenCodeServer` picks a free
  port, writes a generated config dir, spawns the binary (path configurable,
  default the installed `opencode`; submodule build optional), waits for
  `/config` to answer, and is killed on `session` shutdown / app lifespan exit.
- **Config generation** (from TeamGraph): provider block from our config.py
  (lmstudio + deepseek), and one opencode agent per AgentSpec with: `prompt` =
  our `build_instructions(spec)` + neighbor + environment fragments (same
  persona text the native harness uses — see Part A prompt-replace finding);
  `model` {providerID, modelID} from spec.model; `permission` ruleset from
  Capabilities (read/edit/bash globs) PLUS **`question: allow`** (default is
  deny!) and `task: deny` (we use our own ask_agent, not opencode subagents);
  tool toggles to disable webfetch/websearch. Plus `.opencode/tool/ask_agent.ts`
  (the delegation callback). Regenerated + server restarted when the graph/spec
  changes (mirrors native's spec_changed rebuild).
- **One opencode session per team agent** (persistent peer; created lazily,
  id cached per agent_id). `run_to_completion`/`submit` drive via
  `POST /session/{id}/prompt_async` then await our translated `session.idle`.
- **Event translation** (`OpenCode SSE /event` → our bus): subscribe once per
  opencode server; demux by `properties.sessionID` → our agent_id; map
  `message.part.updated` (tool parts by state.status → tool_call/tool_result;
  text/reasoning parts → text/thinking), `message.part.delta` (optional token
  text), `session.status` busy/idle → running/idle lifecycle, `session.idle`
  → agent_done, `question.asked/replied` → user_question/user_question_done,
  `todo.updated` → todos. Update `session.registry` lifecycle badges.
- **history**: GET /session/{id}/message → render parts into our row shapes;
  instructions from GET /agent (resolved prompt) + /api/session/{id}/context.
  clear = delete+recreate the opencode session (or fork from empty). summarize
  = native `POST /session/{id}/summarize`.
- **ask_user**: opencode's native question tool fires `question.asked`; we
  translate to user_question, the UI answers via our endpoint →
  `POST /question/{id}/reply {answers:[[label]]}`. Inject our "use the question
  tool; never end a turn with a plain-text question" guidance into agent.prompt
  (smoke test showed small models need it).
- **ask_agent**: `.opencode/tool/ask_agent.ts` → `POST /internal/ask_agent`
  (localhost, token-guarded) → shared `check_delegation` guards →
  `harness.run_to_completion(target)` on the target's persistent opencode
  session → a2a_message + waiting-on-agent. Same guard semantics as native.
- **usage**: sum per-message `tokens` from message.updated events into a tally.

## B.3 Testing (no real LLM, no real opencode server)
- Native suite stays as-is (must remain green through the refactor).
- OpenCode harness: a **fake in-process opencode server** (FastAPI ASGI app via
  httpx ASGITransport, injected as the harness's HTTP client base) implementing
  the subset we use: POST /session, POST /session/{id}/prompt_async (drives a
  scripted parts script + emits SSE), GET /event (SSE), GET /session/{id}/message,
  GET /agent, /question + reply, abort, /config. The script is a sequence of
  "turns" (tool/text parts) like make_sequence_model, so the same deterministic
  style drives opencode-harness E2E tests: task→tool_call→done, ask_user
  park+resume, ask_agent A→B+guards, history/clear/summarize, lifecycle+usage.
  The OpenCodeServer process-spawn is bypassed in tests (inject base_url + skip
  spawn). Live verification stays manual (local model first, deepseek fallback).

## B.4 Phases & verifiable gates (todos #18–#25)
1. Harness ABC + NativeHarness + route api/wiring through session.harness.
   GATE: all 122 tests green; behavior identical.
2. OpenCode server mgr + config gen from TeamGraph.
   GATE: server boots, GET /agent reflects our agents+perms, ask_agent.ts loaded.
3. OpenCodeHarness run/stop/history/usage + event translation.
   GATE: mocked E2E run→tool_call→bus events→done; one LIVE local-model write task.
4. ask_user translation. GATE: mocked park+resume; live question round-trip.
5. ask_agent delegation + guards. GATE: mocked A→B + cycle/depth reject; live A→B.
6. Full mocked E2E parity suite. GATE: green, deterministic, mirrors native spine.
7. Frontend harness toggle + verify_ui both harnesses. GATE: browser-verified.
8. Docs + adversarial review. GATE: parity/race/leak review clean.

## B.5 Gotchas (live-verified on 1.16.2 — code against these)
- `prompt_async` → HTTP 204, empty body. Drive completion off SSE `session.idle`.
- SSE: each frame is `data: {id,type,properties}\n\n` (no `event:` line); type is
  inside JSON; `sessionID` is inside `properties`. One global stream per server.
- Tool lifecycle streams via `message.part.updated` carrying the full `tool`
  part each transition; key by `part.callID`, diff `part.state.status`
  (pending→running→completed|error). Text via `message.part.delta {field,delta}`.
- Two `model` key conventions: create-session uses `{id, providerID}`;
  prompt/message/agent use `{modelID, providerID}`.
- `question` permission DEFAULTS TO `deny` on agents — must set `allow` or
  ask_user never fires. `.env` reads default `ask`/deny; `external_directory`
  and `doom_loop` default `ask`.
- Custom tool dir is `.opencode/tool/` (SINGULAR) on 1.16.2.
- `agent.prompt`, if set, REPLACES the built-in system prompt entirely
  (`packages/opencode/src/session/llm/request.ts:60`).
- `/context` is `/api/`-prefixed only; returns `{data:[SessionMessage]}` (a
  runtime/system view, NOT the message transcript).
- Live 1.16.2 emits the CLASSIC event family (message.part.updated/delta), not
  the spec's `session.next.*` V2 family — code against classic, tolerate both.

---

## B.6 Phase 3 live findings (2026-06-13) — the cwd gotcha (CRITICAL)

Building + live-testing the harness surfaced THE load-bearing OpenCode gotcha,
now fixed:

- **`prompt_async` only starts a run when the server's working directory equals
  the session's directory.** Proven on 1.16.2: cwd==dir → user+assistant
  messages appear; cwd≠dir (server cwd = a separate config home, session via
  `?directory=repo`) → `prompt_async` returns 204 but creates **zero messages**
  and never runs. (This is the OpenCode multi-directory weakness, issue #12271.)
  It silently looked like a hung model — it was not; a tiny model (qwen3-1.7b)
  isolated it model-independently (no user message ever recorded).
- **Fix (server.py):** run `opencode serve` with **cwd = the repo**. To keep the
  repo clean, the config is passed inline via **`OPENCODE_CONFIG_CONTENT`** (no
  `opencode.json` file), and only the ask_agent tool is written to
  `<repo>/.opencode/tool/ask_agent.ts`. On shutdown a `.opencode` WE created is
  removed wholesale (OpenCode `bun install`s the tool's `@opencode-ai/plugin`
  dep into `.opencode/node_modules`); a pre-existing user `.opencode` is left
  alone (only our tool file removed). Verified: live run creates `hello.txt`,
  streams all bus events, and leaves the repo empty after shutdown.
- **DeepSeek model id**: OpenCode's registry does NOT know
  `deepseek-v4-flash` (its DeepSeek provider exposes models.dev ids like
  `deepseek-chat`/`deepseek-reasoner`, plus its own gateway
  `opencode/deepseek-v4-flash-free`). `prompt_async` silently no-ops on an
  unknown model. So the OpenCode harness + DeepSeek needs a model id OpenCode
  knows — TODO: map our `deepseek:deepseek-v4-flash` to a recognized id (or use
  the opencode gateway model) in config gen. The NATIVE harness keeps using
  `deepseek-v4-flash` directly (works). Local LM Studio models work on both.
- **Known cost**: the tool's dep install runs per session on first server boot
  (a few seconds). Future optimization: a persistent isolated tool dir via
  `XDG_CONFIG_HOME` (probed; first-boot install made readiness flaky, deferred).
- **Live-verified**: run_to_completion, SSE→bus (user_message/thinking/text/
  tool_call/tool_result/agent_done), tool execution (write), history rows,
  usage, clean teardown — all green against `opencode 1.16.2` + qwen3-1.7b.
