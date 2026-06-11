"""The dev toolset: read/write/edit/list/grep/bash.

Two layers, both here so they stay close:

1. **Pure helpers** (``resolve_in_root``, ``path_matches``, ``numbered_slice``,
   ``hash_lines``, ``apply_line_edit``) — no I/O, take inputs and return
   outputs. These are the load-bearing, most-tested logic: the sandbox path
   check, the glob check, the edit math, and the staleness hash.

2. **``DevTools``** — binds a repo ``root`` + a ``Capabilities`` profile + an
   injected write-lock and bash-runner, and exposes the actual operations. The
   *edit* tool is the highest-leverage piece (a line-range edit with a
   content-hash staleness check): it avoids re-emitting surrounding code (context
   saving) and rejects edits against a stale view of a file that another agent
   changed (free optimistic-concurrency, which matters because agents share one
   repo).

``capabilities.py`` turns a ``DevTools`` into a per-agent ``FunctionToolset``,
exposing only the operations the profile allows. The honest caveat from the
spec holds: path-check + cwd is not an escape-proof boundary (``run_bash`` with
absolute paths / ``cd ..`` can leave the repo); accepted for v1.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import Awaitable, Callable

from ..domain.models import Capabilities
from .context_files import ProjectContext

READ_LINE_CAP = 2000
"""Max lines returned by a single read (à la Pi's ~3000 cap)."""

BASH_OUTPUT_CAP = 50_000
"""Truncate bash output beyond this many chars (Pi truncates ~50KB)."""

GREP_RESULT_CAP = 200


# --- pure helpers -----------------------------------------------------------


def resolve_in_root(root: Path, path: str) -> Path:
    """Resolve ``path`` (relative to ``root``) and ensure it stays inside the
    repo. Raises ``ValueError`` otherwise. Works for not-yet-existing paths
    (writes), since only existing path components are symlink-resolved.
    """
    root = root.resolve()
    full = (root / path).resolve()
    if full != root and not full.is_relative_to(root):
        raise ValueError(f"path '{path}' is outside the repo root")
    return full


def path_matches(path: str, globs: list[str]) -> bool:
    """True if ``path`` matches any of the glob patterns. ``**`` matches all."""
    norm = path.lstrip("./")
    for g in globs:
        if g in ("**", "**/*") or fnmatch(norm, g) or fnmatch(path, g):
            return True
    return False


def effective_range(n_lines: int, start_line: int, end_line: int | None,
                    cap: int = READ_LINE_CAP) -> tuple[int, int, bool]:
    """Clamp a requested [start, end] range to what actually exists and to the
    line cap. Returns ``(start, end, truncated)``. Pure — the single source of
    truth for both numbering and the edit-token hash so they never disagree.
    """
    start = max(1, start_line)
    end = n_lines if end_line is None else min(end_line, n_lines)
    truncated = False
    if end - start + 1 > cap:
        end = start + cap - 1
        truncated = True
    return start, end, truncated


def numbered_slice(content: str, start_line: int = 1, end_line: int | None = None,
                   cap: int = READ_LINE_CAP) -> str:
    """Return ``content``'s lines [start, end] as ``<n>\\t<text>`` rows, 1-indexed
    and capped. ``start_line`` < 1 is clamped to 1.
    """
    lines = content.splitlines()
    start, end, truncated = effective_range(len(lines), start_line, end_line, cap)
    rows = [f"{start + i}\t{line}" for i, line in enumerate(lines[start - 1 : end])]
    out = "\n".join(rows)
    if truncated:
        out += f"\n... (truncated at {cap} lines; re-read with a narrower range)"
    return out


def hash_lines(lines: list[str]) -> str:
    """A short content hash of a list of lines — the staleness anchor for edits.
    Joining with ``\\n`` is deterministic and ignores trailing-newline noise.
    """
    digest = hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()
    return digest[:12]


def apply_line_edit(content: str, start_line: int, end_line: int, new_content: str) -> str:
    """Pure line-range replacement. Replaces lines [start, end] (1-indexed,
    inclusive) with ``new_content``. Preserves a trailing newline if the
    original had one.
    """
    if start_line < 1 or end_line < start_line:
        raise ValueError(f"invalid line range {start_line}..{end_line}")
    lines = content.splitlines()
    if start_line > len(lines) + 1:
        raise ValueError(f"start_line {start_line} is past end of file ({len(lines)} lines)")
    new_lines = new_content.splitlines()
    edited = lines[: start_line - 1] + new_lines + lines[end_line:]
    text = "\n".join(edited)
    if content.endswith("\n"):
        text += "\n"
    return text


# --- DevTools (binds root + caps + injected effects) ------------------------

BashRunner = Callable[[str, Path], tuple[int, str, str]]


def _default_bash_runner(command: str, cwd: Path) -> tuple[int, str, str]:
    r = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=str(cwd))
    return r.returncode, r.stdout, r.stderr


class DevTools:
    """Operations over a repo root, enforcing the capability profile. Pure
    helpers above do the logic; this layer does the I/O and the glob checks.
    """

    def __init__(
        self,
        root: Path,
        caps: Capabilities,
        *,
        write_lock: asyncio.Lock | None = None,
        bash_runner: BashRunner = _default_bash_runner,
        project_context: ProjectContext | None = None,
    ):
        self.root = Path(root).resolve()
        self.caps = caps
        self._lock = write_lock or asyncio.Lock()
        self._bash = bash_runner
        # When provided (RunningAgent passes its per-conversation tracker),
        # read_file injects the AGENTS.md/CLAUDE.md files governing what was
        # read — see context_files.py. None = no injection (bare tool tests).
        self._project_context = project_context

    # reads -------------------------------------------------------------

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Read numbered lines [start, end] and append an *edit-token* the model
        copies verbatim into ``edit_file`` to edit exactly these lines. The token
        encodes the range + a content hash, so an edit against a stale view (the
        file changed since this read) is rejected before it can corrupt anything.
        """
        if not path_matches(path, self.caps.read_paths):
            raise ValueError(f"no read access to '{path}'")
        full = resolve_in_root(self.root, path)
        if not full.is_file():
            raise ValueError(f"not a file: '{path}'")
        content = full.read_text()
        lines = content.splitlines()
        body = numbered_slice(content, start_line, end_line)
        start, end, _ = effective_range(len(lines), start_line, end_line)
        token = hash_lines(lines[start - 1 : end])
        out = f"{body}\n[edit-token {start}-{end} {token}]"
        # First read under a directory pulls in its governing AGENTS.md/
        # CLAUDE.md guidance. Prepended so the edit-token stays LAST — models
        # copy the trailing token into edit_file.
        if self._project_context is not None:
            blocks = self._project_context.blocks_for(full)
            if blocks:
                out = "\n\n".join(blocks) + "\n\n" + out
        return out

    def list_dir(self, path: str = ".") -> str:
        """List a directory's entries (subdirectories end with ``/``)."""
        full = resolve_in_root(self.root, path)
        if not full.is_dir():
            raise ValueError(f"not a directory: '{path}'")
        entries = sorted(full.iterdir(), key=lambda p: (p.is_file(), p.name))
        return "\n".join(f"{e.name}/" if e.is_dir() else e.name for e in entries) or "(empty)"

    def grep(self, pattern: str, path: str = ".") -> str:
        """Search file contents under ``path`` with a regular expression.
        Returns matching lines as ``file:line_number:line``."""
        regex = re.compile(pattern)
        base = resolve_in_root(self.root, path)
        results: list[str] = []
        files = [base] if base.is_file() else base.rglob("*")
        for f in files:
            if not f.is_file():
                continue
            rel = str(f.relative_to(self.root))
            if not path_matches(rel, self.caps.read_paths):
                continue
            try:
                text = f.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{rel}:{i}:{line}")
                    if len(results) >= GREP_RESULT_CAP:
                        results.append(f"... (capped at {GREP_RESULT_CAP} matches)")
                        return "\n".join(results)
        return "\n".join(results) or "(no matches)"

    # writes (hold the per-session lock) --------------------------------

    async def write_file(self, path: str, content: str) -> str:
        """Create or fully overwrite a file with ``content`` (parent directories
        are created as needed). For small changes to an existing file, prefer
        ``read_file`` + ``edit_file``."""
        if not path_matches(path, self.caps.write_paths):
            raise ValueError(f"no write access to '{path}'")
        full = resolve_in_root(self.root, path)
        async with self._lock:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
        return f"wrote {path} ({len(content)} bytes)"

    async def edit_file(
        self, path: str, start_line: int, end_line: int, new_content: str, lines_hash: str
    ) -> str:
        """Replace lines [start, end] with ``new_content``. ``lines_hash`` must
        match the hash of the *current* targeted lines (from a prior read),
        else the edit is rejected as stale — re-read before editing.
        """
        if not path_matches(path, self.caps.write_paths):
            raise ValueError(f"no write access to '{path}'")
        full = resolve_in_root(self.root, path)
        if not full.is_file():
            raise ValueError(f"not a file: '{path}'")
        async with self._lock:
            current = full.read_text()
            target = current.splitlines()[start_line - 1 : end_line]
            if hash_lines(target) != lines_hash:
                raise ValueError(
                    "stale: the targeted lines changed since you read them. "
                    "Re-read the file to get a fresh line range and hash, then retry."
                )
            full.write_text(apply_line_edit(current, start_line, end_line, new_content))
        return f"edited {path} lines {start_line}-{end_line}"

    # bash --------------------------------------------------------------

    def run_bash(self, command: str) -> str:
        """Run a shell command in the repository root. Returns ``exit=<code>``
        followed by the command's combined stdout/stderr."""
        code, out, err = self._bash(command, self.root)
        body = f"exit={code}\n{out}{err}"
        if len(body) > BASH_OUTPUT_CAP:
            body = body[:BASH_OUTPUT_CAP] + "\n... (output truncated)"
        return body
