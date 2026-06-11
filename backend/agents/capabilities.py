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

import functools
import inspect
import re

from pydantic_ai import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from .tools import DevTools


def _self_correcting(fn):
    """Surface recoverable tool errors (bad path, no access, stale edit hash,
    bad regex) as ``ModelRetry`` — a nudge the model can act on — instead of a
    plain exception, which pydantic-ai treats as FATAL and kills the whole run
    (this is how every delegated edit with a stale hash used to take the
    target agent down). The DevTools layer keeps raising ``ValueError`` so its
    own tests and any non-agent callers stay framework-free; the conversion
    happens here, at the agent boundary."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapped(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except (ValueError, re.error) as e:
                raise ModelRetry(str(e)) from e
        return async_wrapped

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, re.error) as e:
            raise ModelRetry(str(e)) from e
    return wrapped


def make_dev_toolset(dev: DevTools) -> FunctionToolset:
    """Build the toolset exposing exactly the operations the profile allows."""
    ts: FunctionToolset = FunctionToolset()
    caps = dev.caps

    if caps.can_read:
        ts.add_function(_self_correcting(dev.read_file))
        ts.add_function(_self_correcting(dev.list_dir))
        ts.add_function(_self_correcting(dev.grep))
    if caps.can_write:
        ts.add_function(_self_correcting(dev.write_file))
        ts.add_function(_self_correcting(dev.edit_file))
    if caps.bash:
        ts.add_function(_self_correcting(dev.run_bash))

    return ts


def toolset_tool_names(ts: FunctionToolset) -> set[str]:
    """The tool names a toolset exposes — used by tests to assert the
    profile→toolset shape without standing up a full agent run."""
    return set(ts.tools.keys())
