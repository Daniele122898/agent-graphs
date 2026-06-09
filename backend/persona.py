"""Persona / instructions building.

The persona is *sticky*: it goes in Pydantic AI ``instructions`` (not
``system_prompt``), which are re-inserted on every model request and never lost
to truncated/compacted history. This is the agent's identity that survives
compaction (Phase 6).

Phase 2 builds a static instructions string (persona + how to use the tools).
Phase 4 layers the live graph-neighbor list on top via ``@agent.instructions``
so the agent always knows who it may consult.
"""

from __future__ import annotations

from .models_domain import AgentSpec

TOOL_GUIDANCE = """\
You work inside a single repository and may only touch what your capabilities allow.

Editing files:
- `read_file(path, start_line, end_line)` returns numbered lines and ends with an
  `[edit-token <start>-<end> <hash>]`. To change those exact lines, call
  `edit_file(path, start, end, new_content, lines_hash=<hash>)`, copying the hash
  from the token. Read precisely the lines you intend to replace.
- If `edit_file` reports the edit is "stale", the file changed since you read it —
  re-read the range to get a fresh token, then retry. Do not guess the hash.
- `write_file(path, content)` creates or overwrites a whole file.

Planning:
- For anything non-trivial (roughly 3+ steps) or work you delegate, call
  `write_todos` first to lay out a checklist, and keep it updated as you progress.
  For trivial work, just do it.
"""


def build_instructions(spec: AgentSpec) -> str:
    """The sticky instructions for an agent: its persona, then tool guidance."""
    persona = spec.persona.strip() or f"You are {spec.name}, a software engineering agent."
    return f"{persona}\n\n{TOOL_GUIDANCE}"
