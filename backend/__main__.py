"""Run entrypoint: `python -m backend` (single-process; serves the built UI if
frontend/dist exists) or `python -m backend --reload` (dev, auto-reload).

Why this exists instead of running `uvicorn backend.main:app` directly: the SSE
`/events` stream is an infinite response, so uvicorn's graceful shutdown waits for
that connection to close forever ("Waiting for connections to close") unless a
`timeout_graceful_shutdown` is set. That used to be a CLI flag everyone had to
remember (and forgetting it — e.g. in single-process mode — wedged shutdown).
Here it's BAKED IN, so shutdown is always bounded. (backend/runtime/bus.py also
ends SSE subscribers on shutdown, but the lifespan runs only AFTER the connection
wait, so this cap is the real backstop.)

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
