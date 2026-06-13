"""OpenCode-backed harness.

Drives a headless ``opencode serve`` process (one per session) and translates
its HTTP/SSE surface into the ``Harness`` interface + our event bus.

- ``config.py`` — generate ``opencode.json`` + the ask_agent tool from a TeamGraph.
- ``server.py`` — spawn/own the server process per session.
- ``client.py`` — thin async HTTP/SSE client (the seam tests mock).
- ``render.py`` — OpenCode message parts → our transcript row shapes.
- ``harness.py`` — ``OpenCodeHarness``: sessions, prompts, SSE→bus translation,
  history, usage, ask_user, ask_agent.
"""

from __future__ import annotations

from .config import build_opencode_config
from .harness import OpenCodeHarness

__all__ = ["OpenCodeHarness", "build_opencode_config"]
