"""Function tests for the dev toolset — real behavior against a temp-dir repo.

These exercise the load-bearing safety logic: the sandbox path check, the glob
enforcement, the line-range edit math, and the staleness hash. A failure here
means something is broken (the sandbox leaks, an edit corrupts a file, a stale
edit is accepted), never merely "changed".
"""

from __future__ import annotations

import pytest

from backend.models_domain import Capabilities
from backend.tools import (
    DevTools,
    apply_line_edit,
    hash_lines,
    numbered_slice,
    path_matches,
    resolve_in_root,
)


# --- pure helpers -----------------------------------------------------------


def test_resolve_in_root_allows_inside(repo):
    assert resolve_in_root(repo, "src/app.py") == (repo / "src/app.py").resolve()


def test_resolve_in_root_rejects_escape(repo):
    with pytest.raises(ValueError):
        resolve_in_root(repo, "../outside.txt")
    with pytest.raises(ValueError):
        resolve_in_root(repo, "/etc/passwd")


def test_path_matches_globs():
    assert path_matches("src/app.py", ["src/**"])
    assert path_matches("anything", ["**"])
    assert not path_matches("docs/readme.md", ["src/**"])


def test_numbered_slice_is_1_indexed_and_ranged():
    content = "a\nb\nc\nd\ne"
    assert numbered_slice(content, 2, 4) == "2\tb\n3\tc\n4\td"
    assert numbered_slice(content, 1, 1) == "1\ta"


def test_numbered_slice_caps_long_output():
    content = "\n".join(str(i) for i in range(5000))
    out = numbered_slice(content, 1, None, cap=100)
    assert "truncated at 100 lines" in out
    assert out.count("\n") <= 101


def test_apply_line_edit_replaces_range():
    content = "one\ntwo\nthree\n"
    assert apply_line_edit(content, 2, 2, "TWO") == "one\nTWO\nthree\n"
    # multi-line replacement
    assert apply_line_edit(content, 2, 3, "X\nY\nZ") == "one\nX\nY\nZ\n"


def test_hash_lines_detects_change():
    assert hash_lines(["a", "b"]) == hash_lines(["a", "b"])
    assert hash_lines(["a", "b"]) != hash_lines(["a", "c"])


# --- DevTools reads/writes --------------------------------------------------


def test_read_file_returns_numbered_lines_with_edit_token(repo):
    (repo / "f.txt").write_text("hello\nworld\n")
    dev = DevTools(repo, Capabilities.from_level("read"))
    out = dev.read_file("f.txt")
    assert out.startswith("1\thello\n2\tworld")
    # the appended token encodes the range + the hash the model copies to edit
    assert f"[edit-token 1-2 {hash_lines(['hello', 'world'])}]" in out


async def test_edit_using_token_from_read_succeeds(repo):
    """The intended workflow: read a range, copy the token's hash into edit."""
    (repo / "f.py").write_text("a\nb\nc\n")
    dev = DevTools(repo, Capabilities.from_level("read-write"))
    out = dev.read_file("f.py", 2, 2)
    token_hash = out.rsplit(" ", 1)[1].rstrip("]")
    await dev.edit_file("f.py", 2, 2, "B", token_hash)
    assert (repo / "f.py").read_text() == "a\nB\nc\n"


def test_read_outside_read_paths_is_rejected(repo):
    (repo / "secret.txt").write_text("x")
    caps = Capabilities(filesystem="read", read_paths=["src/**"], write_paths=[], bash=False)
    dev = DevTools(repo, caps)
    with pytest.raises(ValueError, match="no read access"):
        dev.read_file("secret.txt")


async def test_write_file_creates_parent_dirs(repo):
    dev = DevTools(repo, Capabilities.from_level("read-write"))
    await dev.write_file("src/deep/new.py", "print(1)\n")
    assert (repo / "src/deep/new.py").read_text() == "print(1)\n"


async def test_write_outside_write_paths_is_rejected(repo):
    caps = Capabilities(filesystem="read-write", read_paths=["**"], write_paths=["src/**"], bash=False)
    dev = DevTools(repo, caps)
    with pytest.raises(ValueError, match="no write access"):
        await dev.write_file("docs/readme.md", "x")


async def test_edit_file_with_correct_hash_succeeds(repo):
    (repo / "f.py").write_text("a\nb\nc\n")
    dev = DevTools(repo, Capabilities.from_level("read-write"))
    h = hash_lines(["b"])  # line 2
    await dev.edit_file("f.py", 2, 2, "B", h)
    assert (repo / "f.py").read_text() == "a\nB\nc\n"


async def test_edit_file_with_stale_hash_is_rejected(repo):
    (repo / "f.py").write_text("a\nb\nc\n")
    dev = DevTools(repo, Capabilities.from_level("read-write"))
    with pytest.raises(ValueError, match="stale"):
        await dev.edit_file("f.py", 2, 2, "B", "deadbeef0000")
    # file is untouched
    assert (repo / "f.py").read_text() == "a\nb\nc\n"


# --- bash -------------------------------------------------------------------


def test_run_bash_runs_in_repo_cwd(repo):
    dev = DevTools(repo, Capabilities.from_level("read-write"))
    out = dev.run_bash("pwd")
    assert str(repo.resolve()) in out
    assert "exit=0" in out


def test_run_bash_injected_runner_for_determinism(repo):
    calls = []

    def fake(cmd, cwd):
        calls.append((cmd, cwd))
        return 0, "fake-out", ""

    dev = DevTools(repo, Capabilities.from_level("read-write"), bash_runner=fake)
    out = dev.run_bash("anything")
    assert "fake-out" in out
    assert calls == [("anything", repo.resolve())]
