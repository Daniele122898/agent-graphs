"""Capability profile → per-agent toolset.

The model never sees a tool it isn't allowed to use: a read-only agent's toolset
literally contains no ``write_file``/``edit_file``; a no-bash agent never gets
``run_bash``. Enforcement lives here in the tool layer, not in persona prose —
cleaner and cheaper than letting the model try and rejecting.

``make_dev_toolset`` takes an already-bound ``DevTools`` (root + caps + injected
write-lock/bash-runner) so the session owns the effects and tests can inject
fakes.
"""

from __future__ import annotations

from pydantic_ai.toolsets import FunctionToolset

from .tools import DevTools


def make_dev_toolset(dev: DevTools) -> FunctionToolset:
    """Build the toolset exposing exactly the operations the profile allows."""
    ts: FunctionToolset = FunctionToolset()
    caps = dev.caps

    if caps.can_read:
        ts.add_function(dev.read_file)
        ts.add_function(dev.list_dir)
        ts.add_function(dev.grep)
    if caps.can_write:
        ts.add_function(dev.write_file)
        ts.add_function(dev.edit_file)
    if caps.bash:
        ts.add_function(dev.run_bash)

    return ts


def toolset_tool_names(ts: FunctionToolset) -> set[str]:
    """The tool names a toolset exposes — used by tests to assert the
    profile→toolset shape without standing up a full agent run."""
    return set(ts.tools.keys())
