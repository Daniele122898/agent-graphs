"""Pluggable agent-execution harnesses.

The product needs a fixed set of things from "an agent": run a prompt (now or
queued), stop it, read/clear/summarize its transcript, ask the user, delegate
to a teammate, report usage. Those operations are the ``Harness`` interface
(``base.py``), keyed by ``agent_id`` so the caller never holds a harness-specific
worker object. Two implementations live side by side:

- ``native`` (``native.py``) — our pydantic-ai harness (``RunningAgent`` +
  in-process tools); the default.
- ``opencode`` (``opencode/``) — drives a headless OpenCode server.

Selection is per session (config default + per-launch override). A ``Session``
holds one ``Harness``; the HTTP layer and ``wiring`` route every agent operation
through ``session.harness``. The event contract is the session ``bus`` + the
lifecycle ``registry`` — both harnesses publish the same event names/shapes, so
the control room renders identically regardless of harness.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Harness, HistoryView

if TYPE_CHECKING:
    from ..agents.a2a import MessageLog
    from ..storage.agent_state import AgentStateStore

DEFAULT_HARNESS = "native"


def make_harness(
    harness_id: str,
    *,
    state_store: "AgentStateStore",
    message_log: "MessageLog",
    repo_root=None,
) -> Harness:
    """Build the harness for ``harness_id``. ``state_store``/``message_log`` are
    app-level (shared DB) stores the native harness needs; opencode ignores them
    but accepts them for a uniform signature."""
    if harness_id in ("native", "", None):
        from .native import NativeHarness

        return NativeHarness(state_store=state_store, message_log=message_log)
    if harness_id == "opencode":
        from .opencode import OpenCodeHarness  # noqa: F401 — added in Phase 2/3

        return OpenCodeHarness(state_store=state_store, message_log=message_log, repo_root=repo_root)
    raise ValueError(f"unknown harness '{harness_id}' (expected 'native' or 'opencode')")


__all__ = ["Harness", "HistoryView", "make_harness", "DEFAULT_HARNESS"]
