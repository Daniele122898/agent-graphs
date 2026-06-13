"""The system prompt for an OpenCode-backed agent.

OpenCode's `agent.prompt`, when set, REPLACES its built-in system prompt
(`packages/opencode/src/session/llm/request.ts:60`). We replace it with the
SAME identity our native harness uses — persona, team context, capability
summary, live neighbors, environment — so an agent behaves identically on
either harness. The one part we DON'T reuse is the native tool guidance: it
talks about native tools (`ask_user`, `write_file`, edit-tokens) that don't
exist in OpenCode. OpenCode's tools are `read`/`edit`/`write`/`bash`/`grep`/
`glob`/`question`/`ask_agent`, so we substitute guidance written for them.
"""

from __future__ import annotations

from pathlib import Path

from ...agents.a2a import neighbor_instructions
from ...agents.persona import TEAM_CONTEXT, capability_summary, environment_instructions
from ...domain.models import AgentSpec, TeamGraph

OPENCODE_TOOL_GUIDANCE = """\
You work inside a single repository and may only touch what your capabilities allow.

Tools:
- Use `read` to read files, `edit`/`write` to change them, `grep`/`glob`/`list`
  to search, and `bash` to run commands (only those your capabilities permit).
- Always read a file before editing it.
- For anything non-trivial or work you delegate, keep a todo list with the todo
  tool and update it as you progress.

Working with the user:
- When you need a decision, preference, or missing information from the user,
  call the `question` tool (offer options where sensible) and WAIT for the
  answer. NEVER end your turn with a question written as plain text — the user
  cannot answer that and the work stalls.

Working with teammates:
- Consult the teammates listed below with the `ask_agent(target_id, question)`
  tool instead of guessing about a domain they own. Do NOT seek out AGENTS.md /
  CLAUDE.md files yourself; the harness surfaces relevant project context.

Keep working until the task is complete or you are genuinely blocked. Do not
stop midway to narrate what you plan to do next — do it.\
"""


def build_opencode_prompt(spec: AgentSpec, graph: TeamGraph, repo_root: Path) -> str:
    """The full replaced system prompt for an OpenCode agent: the shared
    identity sections + OpenCode-appropriate tool guidance + the dynamic
    neighbor/environment fragments folded in (OpenCode has no per-run instruction
    hook, so they are baked at config-generation time and refreshed when the
    graph changes)."""
    persona = spec.persona.strip() or f"You are {spec.name}, a software engineering agent."
    sections = [
        persona,
        TEAM_CONTEXT,
        OPENCODE_TOOL_GUIDANCE,
        capability_summary(spec.capabilities),
        neighbor_instructions(graph, spec.id),
        environment_instructions(spec, repo_root),
    ]
    return "\n\n".join(s for s in sections if s)
