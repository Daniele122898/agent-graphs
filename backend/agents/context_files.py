"""Project context files (AGENTS.md / CLAUDE.md), loaded the way Claude Code
loads its memory files.

Repositories carry standing instructions for coding agents in per-directory
markdown files. Different tools name them differently — Claude Code reads
``CLAUDE.md``, the cross-tool convention is ``AGENTS.md`` — so both are
supported: **per directory, AGENTS.md wins and CLAUDE.md is ignored** when both
exist.

Loading is *lazy and additive*, mirroring Claude Code: nothing is loaded up
front; when an agent reads a file, the context files governing that file — one
per directory from the session repo root down to the file's own directory —
are injected into the tool result, root first (general → specific). Each block
is wrapped in delimiters naming its source folder and stating that it applies
only to that subtree. A per-conversation tracker (``ProjectContext``, owned by
the ``RunningAgent``) dedupes so each file enters the conversation once;
clearing/summarizing the history resets it (the blocks are gone from context,
so they must be re-injectable).
"""

from __future__ import annotations

from pathlib import Path

CONTEXT_FILENAMES = ("AGENTS.md", "CLAUDE.md")
"""Per-directory candidates, in priority order: AGENTS.md shadows CLAUDE.md."""

CONTEXT_FILE_CHAR_CAP = 10_000
"""Per-file cap — a runaway context file must not drown a small local model."""


def governing_context_files(root: Path, target: Path) -> list[Path]:
    """The context files governing ``target``: for each directory from ``root``
    down to the file's own directory, AGENTS.md if present else CLAUDE.md.
    Ordered root-first so general guidance precedes specific. Pure lookup."""
    root = root.resolve()
    directory = target.parent
    if directory != root and not directory.is_relative_to(root):
        return []
    chain = [directory]
    while chain[-1] != root:
        chain.append(chain[-1].parent)
    found: list[Path] = []
    for d in reversed(chain):
        for name in CONTEXT_FILENAMES:
            candidate = d / name
            if candidate.is_file():
                found.append(candidate)
                break  # AGENTS.md wins; ignore CLAUDE.md in the same directory
    return found


def render_context_block(file: Path, root: Path) -> str:
    """One delimited context block: where it came from, what subtree it
    applies to, and its (capped) contents."""
    rel = file.relative_to(root)
    folder = str(rel.parent)
    scope = (
        "the whole repository"
        if folder == "."
        else f"the '{folder}/' folder and everything below it"
    )
    body = file.read_text()
    if len(body) > CONTEXT_FILE_CHAR_CAP:
        body = body[:CONTEXT_FILE_CHAR_CAP] + "\n... (truncated)"
    return (
        f"[project context from {rel} — these instructions apply ONLY to {scope}]\n"
        f"{body.rstrip()}\n"
        f"[end of project context from {rel}]"
    )


class ProjectContext:
    """Per-conversation tracker of which context files are already in context.

    Owned by the ``RunningAgent`` (one conversation = one tracker) and injected
    into its ``DevTools``; ``reset()`` on history clear/summarize so the
    guidance can re-enter the fresh conversation.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._seen: set[Path] = set()

    def reset(self) -> None:
        self._seen.clear()

    def blocks_for(self, target: Path) -> list[str]:
        """Delimited blocks for the not-yet-loaded context files governing
        ``target``, marking them loaded. Reading a context file itself counts
        as loading it (the model just saw the content) without a block."""
        blocks: list[str] = []
        for f in governing_context_files(self.root, target):
            if f in self._seen:
                continue
            self._seen.add(f)
            if f == target:
                continue
            try:
                blocks.append(render_context_block(f, self.root))
            except OSError:  # a vanished/unreadable context file never breaks a read
                continue
        return blocks
