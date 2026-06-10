# Pi Harness Learnings — System Prompt Construction

**Pi** (`~/code/pi`) is a TypeScript coding-agent harness (the generic
`packages/agent` harness plus the `packages/coding-agent` CLI built on it). It is
a single-agent, terminal-first tool — a different shape from agent-graphs — but
its system-prompt assembly is unusually clean, and we studied it as inspiration
for how our own harness (FastAPI + Pydantic AI) should build per-agent
instructions. Findings below were verified against the source; file:line refs
point into the pi repo.

## How pi assembles the system prompt

`buildSystemPrompt()` (`packages/coding-agent/src/core/system-prompt.ts:28-173`)
concatenates sections in a fixed order:

1. **Role / persona** — "You are an expert coding assistant operating inside
   pi…" (line 130). A `customPrompt` replaces this default wholesale but still
   receives sections 5–8 below.
2. **Available tools list** — one line per tool, `- name: snippet`. Crucially, a
   tool appears here **only if the caller registered a one-line snippet** for it
   in `toolSnippets` (lines 89–93): *visibility in the prompt is decoupled from
   availability in the API*. Tools without snippets still work; the prompt adds
   "you may have access to other custom tools" as a catch-all.
3. **Guidelines** — bullets assembled conditionally from which tools are present
   (e.g. "use bash for ls/rg/find" only when bash exists but grep/find/ls don't),
   plus caller-supplied `promptGuidelines`, deduplicated (lines 95–128).
4. **Docs paths** — absolute paths to pi's own README/docs/examples, with
   instructions to read them only when the user asks about pi itself (lines 140–147).
5. **Appended text** — the `appendSystemPrompt` block (lines 149–151).
6. **Project context files** — AGENTS.md-likes, each wrapped in
   `<project_instructions path="...">` inside one `<project_context>` block
   (lines 153–161). The `path` attribute gives the model *provenance* for every
   instruction it follows.
7. **Skills** — `<available_skills>` XML (name/description/location per skill,
   agentskills.io format; `skills.ts:335`). Only emitted if the `read` tool is
   available, since skills are loaded by reading their SKILL.md (line 164).
8. **Environment info LAST** — current date (`YYYY-MM-DD`) and cwd appended at
   the very end (lines 168–170), so the freshest, most run-specific facts sit
   closest to the conversation.

## Mechanics: static vs. callback, rebuilt per turn

- `AgentHarness` accepts `systemPrompt` as a **static string OR an async
  callback** receiving `(env, session, model, thinkingLevel, activeTools,
  resources)` (`packages/agent/src/harness/agent-harness.ts:339-351`).
- The prompt is **rebuilt at the start of every turn** (inside
  `createTurnState`) and is **never persisted in session storage** — only
  messages are. Change a config, skill, or context file mid-session and the
  next turn picks it up automatically; resumed sessions get a current prompt,
  not a stale snapshot.
- Two constrained extension points, both narrower than "mutate anything":
  - Extensions can replace the prompt per-turn via the `before_agent_start`
    hook — the hook receives the built prompt and may return a substitute
    (`agent-harness.ts:570-577`, applied at 589).
  - Resource loaders expose `systemPromptOverride` (replace the base) and
    `appendSystemPromptOverride` (append-only `string[]`, joined with `\n\n`)
    (`packages/coding-agent/src/core/resource-loader.ts:152-153`, joined in
    `agent-session.ts:914-915`).

## Clever patterns worth copying

- **Dynamic tool visibility via snippets.** What the model is *told about* is a
  deliberate, separate decision from what it *can call*. Lets you keep rarely
  needed tools out of the prompt without removing them.
- **XML wrapping with provenance.** `<project_instructions path="...">` makes
  multi-source context auditable — the model (and a human reading the prompt)
  can see where each rule came from.
- **Append-only extension points.** `appendSystemPromptOverride` returns a
  `string[]` you can only extend — extensions compose instead of fighting over
  one mutable string. Full replacement exists but is a distinct, explicit API.
- **Environment info appended last.** Date and cwd go at the very end, freshly
  computed each build — never baked into a cached persona.
- **Prompt rebuilt every turn, never persisted.** The prompt is a pure function
  of current config; edits take effect immediately and resume can't go stale.

## What agent-graphs adopts

| Pi learning | Our equivalent |
|---|---|
| Sectioned prompt assembly in one pure function | `backend/persona.py` `build_instructions()` builds the sticky persona + tool guidance as ordered sections — keep it a pure function of the `AgentSpec`, extend with new sections rather than ad-hoc string surgery. |
| Per-turn rebuild via callback | Pydantic AI's `@agent.instructions` functions are our callback equivalent: re-evaluated every run. `backend/agents.py` already uses one for the live neighbor list (`_neighbors`, via `a2a.neighbor_instructions`); date/env fragments belong in the same mechanism, never in the static persona. |
| Tool visibility decoupled from availability | We go one step further: the per-agent toolset is *generated* from the capability profile, so unavailable tools don't exist at all. The pi lesson we keep is the **one-line capability summary in the instructions** — tell each agent what it can read/write/run instead of letting it discover limits by trial-and-error tool failures. |
| Environment info last | Any dynamic environment fragment (date, repo path, session facts) is emitted by `@agent.instructions` functions, which Pydantic AI appends *after* the static instructions — same freshness-last ordering for free. |
| Provenance-wrapped context | If/when we inject repo context files (CLAUDE.md-likes) into agent instructions, wrap each in an XML tag carrying its source path, pi-style. |
| Append-only extensibility | Future persona extension points (per-team prompt addenda, user overrides) should be append-only lists joined at build time, with full replacement as a separate explicit option — not direct mutation of the built string. |

What we deliberately do **not** copy: pi's skills system and docs-path section
are single-agent CLI concerns; our equivalent of "who can do what" is the graph
plus capability profiles, injected per-agent rather than described globally.
