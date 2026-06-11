# backend/agents/ — building one agent

`factory.py` (build_agent), `persona.py` (sticky instructions), `tools.py`
(DevTools: sandboxed fs + bash), `capabilities.py` (profile → toolset),
`todos.py` (write_todos + AgentDeps), `a2a.py` (ask_agent delegation),
`questions.py` (ask_user + QuestionBoard), `history.py` (compaction +
transcript rendering), `context_files.py` (AGENTS.md/CLAUDE.md loading).

## Invariants (the *why*)
- **Recoverable tool errors MUST surface as `ModelRetry`, never plain
  exceptions** — pydantic-ai treats any other exception as fatal and kills the
  whole run (this is how every delegated edit with a stale hash used to take
  the target agent down). `DevTools` keeps raising `ValueError`
  (framework-free, directly testable); the conversion happens at the agent
  boundary in `capabilities._self_correcting`. Agents get `retries=3` (small
  models need a few self-correction rounds).
- **Per-agent toolset from the capability profile** (`capabilities.py`): a
  read-only agent's toolset literally has no write/edit tool. Enforcement is in
  the tool layer, never in persona prose. Introspect via `ts.tools`.
- **The edit tool** (`tools.py`): line-range edit + content hash. `read_file`
  appends an `[edit-token <start>-<end> <hash>]` the model copies into
  `edit_file` (weak local models can't compute hashes). `effective_range` is
  the single source of truth shared by numbering and the token hash. Edit
  tokens are personal — the persona tells agents to re-read before editing and
  never to dictate hashes/line numbers when delegating.
- **Persona is sticky**: it goes in Pydantic AI `instructions` (rebuilt every
  request), never `system_prompt`, so it survives history compaction and
  clears. Dynamic facts (neighbors, environment) are emitted per run by
  `@agent.instructions` fragments registered in `factory.py` — freshest context
  last, and graph/config edits take effect on the next run.
- **Delegation guards live in code** (`a2a.py`): target must be a graph
  neighbor, no cycles, depth capped — each raises `ModelRetry` so the model
  self-corrects. Consultation failures publish a `[consultation failed: ...]`
  reply so the message log shows WHY, not just a generic retries-exceeded.
- **ask_user lifecycle** (`questions.py`): the board parks the run on an
  asyncio Future, sets `waiting-on-user`, restores `running` after; answers
  must match the question count; a timeout returns a "proceed on your best
  judgment" tool result; a restart cancels pending questions with the run.
- **Project context files load lazily, Claude Code-style** (`context_files.py`):
  `read_file` prepends the AGENTS.md/CLAUDE.md files governing the file being
  read (one per directory from the session repo root down; **AGENTS.md shadows
  CLAUDE.md per directory**), each wrapped in delimiters naming its folder and
  scope. Once per conversation: the tracker lives on the `RunningAgent`
  (`project_context`) and resets on `replace_history` (clear/summarize) so the
  guidance can re-enter a fresh conversation. The edit-token must stay the LAST
  line of a read result — blocks are prepended, never appended.
- **Local-model prompt hygiene**: every tool needs a docstring (it becomes the
  OpenAI tool description — an empty one starves small models of guidance).
- **Compaction** (`history.py`) only trims the conversation, never instructions,
  and only cuts at a clean user-prompt boundary (never inside a tool-call/
  tool-return pair). `render_messages` flattens stored messages into the same
  row shapes the live SSE events use, so the UI renders past and live work
  identically.
