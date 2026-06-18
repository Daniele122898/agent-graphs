"""Run entrypoint: `python -m backend` (single-process; serves the built UI if
frontend/dist exists) or `python -m backend --reload` (dev, auto-reload).

Why this exists instead of running `uvicorn backend.main:app` directly: it serves
the built UI in single-process mode, and it bakes in `timeout_graceful_shutdown`
as DEFENSE-IN-DEPTH for the infinite SSE `/events` stream (which would otherwise
wedge uvicorn's graceful shutdown at "Waiting for connections to close" — its
default is None = wait forever).

The REAL backstop now lives in the app, not this flag: `backend.main.
_install_sse_shutdown` closes the SSE buses the instant SIGINT/SIGTERM lands
(before the connection wait), so shutdown is bounded for ANY launcher — that's
the fix that finally stopped this hang from recurring whenever someone forgot the
flag. Keep BOTH: the in-app handler is the guarantee, this flag is the seatbelt.

Env: AGENT_GRAPHS_PORT (default 8000), AGENT_GRAPHS_HOST (default 127.0.0.1),
AGENT_GRAPHS_GRACEFUL_TIMEOUT (default 3).
"""

from __future__ import annotations

import os
import sys

import uvicorn


def main() -> None:
    reload = "--reload" in sys.argv
    uvicorn.run(
        "backend.main:app",
        host=os.environ.get("AGENT_GRAPHS_HOST", "127.0.0.1"),
        port=int(os.environ.get("AGENT_GRAPHS_PORT", "8000")),
        reload=reload,
        timeout_graceful_shutdown=int(os.environ.get("AGENT_GRAPHS_GRACEFUL_TIMEOUT", "3")),
    )


if __name__ == "__main__":
    main()
