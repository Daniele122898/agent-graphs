"""Profile → toolset shape. The model must never receive a tool it can't use.

Failures here mean the enforcement boundary broke (a read-only agent gained a
write tool) — a real safety regression, not a cosmetic change.
"""

from __future__ import annotations

from backend.agents.capabilities import make_dev_toolset, toolset_tool_names
from backend.domain.models import Capabilities
from backend.agents.tools import DevTools


def _names(caps: Capabilities, repo) -> set[str]:
    return toolset_tool_names(make_dev_toolset(DevTools(repo, caps)))


def test_read_only_has_no_write_tools(repo):
    names = _names(Capabilities(filesystem="read", read_paths=["**"], write_paths=[], bash=False), repo)
    assert "read_file" in names
    assert "grep" in names
    assert "write_file" not in names
    assert "edit_file" not in names
    assert "run_bash" not in names


def test_read_write_has_full_toolset(repo):
    names = _names(Capabilities.from_level("read-write"), repo)
    assert {"read_file", "list_dir", "grep", "write_file", "edit_file", "run_bash"} == names


def test_no_filesystem_means_no_file_tools(repo):
    names = _names(Capabilities(filesystem="none", read_paths=[], write_paths=[], bash=True), repo)
    assert names == {"run_bash"}  # only bash


def test_bash_toggle_controls_bash_tool(repo):
    with_bash = _names(Capabilities(filesystem="read", read_paths=["**"], write_paths=[], bash=True), repo)
    without_bash = _names(Capabilities(filesystem="read", read_paths=["**"], write_paths=[], bash=False), repo)
    assert "run_bash" in with_bash
    assert "run_bash" not in without_bash
