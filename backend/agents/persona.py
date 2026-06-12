"""Persona / instructions building.

The persona is *sticky*: it goes in Pydantic AI ``instructions`` (not
``system_prompt``), which are re-inserted on every model request and never lost
to truncated/compacted history. This is the agent's identity that survives
compaction (Phase 6).

Following the pi harness study (specs/pi-harness-learnings.md), instructions
are assembled as ordered sections by one pure function of the ``AgentSpec``:
persona → team context → tool guidance → capability summary. Dynamic facts
(live neighbor list, repo root, OS, date) are *not* baked in here — they are
emitted per run by ``@agent.instructions`` fragments (see factory.py), which
Pydantic AI appends after the static instructions, so the freshest context
sits last and graph/config edits take effect on the next run.
"""

from __future__ import annotations

import platform
from datetime import date
from pathlib import Path

from ..domain.models import AgentSpec, Capabilities

TEAM_CONTEXT = """\
You are one agent in a multi-agent software team sharing a single repository.
Teammates may consult you via ask_agent: answer such questions concisely from
your own expertise. You may likewise consult the teammates listed under
"Teammates you can consult" (if any) instead of guessing about their domains.\
"""

TOOL_GUIDANCE = """\
You work inside a single repository and may only touch what your capabilities allow.

Tool calls:
- Invoke tools ONLY through the function-calling interface. Never write a tool
  call as text, pseudo-code, or inside a code fence — text like
  `write_file(...)` does nothing and the work will NOT happen.
- After calling a tool, read its result before claiming success.

Editing files:
- `read_file(path, start_line, end_line)` returns numbered lines and ends with an
  `[edit-token <start>-<end> <hash>]`. To change those exact lines, call
  `edit_file(path, start, end, new_content, lines_hash=<hash>)`, copying the hash
  from the token. Read precisely the lines you intend to replace.
- If `edit_file` reports the edit is "stale", the file changed since you read it —
  re-read the range to get a fresh token, then retry. Do not guess the hash.
- Edit tokens are yours alone: ALWAYS read the file yourself right before
  editing. Never use a hash quoted by a teammate or remembered from earlier —
  it will be stale. When delegating an edit, describe the change; do not
  dictate line numbers or hashes.
- `write_file(path, content)` creates or overwrites a whole file.

Project context files:
- `read_file` results may begin with `[project context from <path> ...]` blocks:
  standing instructions from the repository's maintainers, injected for you
  AUTOMATICALLY the first time you read a file they govern. Follow them; each
  block applies ONLY to the folder it names and everything below it.
- Do NOT seek out or read AGENTS.md / CLAUDE.md files yourself — the harness
  delivers them when they are relevant, and many repositories have none.

Planning:
- For anything non-trivial (roughly 3+ steps) or work you delegate, call
  `write_todos` first to lay out a checklist, and keep it updated as you progress.
  For trivial work, just do it.

Working with the user:
- If you need a decision, preference, or missing information from the user,
  call `ask_user` (offer options where sensible) and WAIT for the answer.
  NEVER end your turn with questions written as plain text — the user cannot
  answer those, and the work stalls.
- Keep working until the task is complete or you are genuinely blocked. Do not
  stop midway to narrate what you plan to do next — do it.\
"""


def capability_summary(caps: Capabilities) -> str:
    """A human-readable statement of what this agent may touch, so it plans
    within its limits instead of discovering them through failed tool calls."""
    if not caps.can_read:
        fs = "- Filesystem: no access — you cannot read or write files."
    elif not caps.can_write:
        fs = "- Filesystem: read-only — you can read files but have no write/edit tools."
    else:
        fs = "- Filesystem: read & write."
    lines = [fs]
    if caps.can_read and caps.read_paths != ["**"]:
        lines.append(f"- Readable paths (globs): {', '.join(caps.read_paths)}")
    if caps.can_write and caps.write_paths != ["**"]:
        lines.append(f"- Writable paths (globs): {', '.join(caps.write_paths)}")
    lines.append(
        "- Shell: `run_bash` is available." if caps.bash
        else "- Shell: not available — you have no bash tool."
    )
    return "Your capabilities:\n" + "\n".join(lines)


def build_instructions(spec: AgentSpec) -> str:
    """The sticky instructions for an agent, as ordered sections: persona,
    team context, tool guidance, capability summary. Pure function of the spec
    — rebuild the agent (RunningAgent does this on spec change) to refresh."""
    persona = spec.persona.strip() or f"You are {spec.name}, a software engineering agent."
    return "\n\n".join([persona, TEAM_CONTEXT, TOOL_GUIDANCE, capability_summary(spec.capabilities)])


def environment_instructions(spec: AgentSpec, repo_root: Path) -> str:
    """The per-run environment fragment (emitted by ``@agent.instructions`` so
    it is re-evaluated every run and appended last, pi-style)."""
    return (
        "Environment:\n"
        f"- You are `{spec.id}` (\"{spec.name}\").\n"
        f"- Repository root (your working directory; tool paths are relative to it): {repo_root}\n"
        f"- Operating system: {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"- Today's date: {date.today().isoformat()}"
    )
