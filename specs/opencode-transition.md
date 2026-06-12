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
