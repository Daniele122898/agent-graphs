"""EventBus — a tiny in-memory async pub/sub, owned per ``Session``.

Every observable thing (agent text/thinking/tool events, lifecycle changes,
task transitions, inter-agent messages, "waiting for model slot") is published
here and fanned out to subscribers. The SSE endpoint (streaming.py) subscribes
and forwards to the browser. Keeping it per-session means events from one repo
never leak into another's stream.

Deliberately minimal: an unbounded ``asyncio.Queue`` per subscriber. Local,
single-user, low volume — backpressure and bounded buffers are not a concern
yet. Every event carries the ``session_id`` so a multiplexed client can route.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator


_CLOSE = object()  # sentinel pushed to wake + end a subscriber on shutdown


class EventBus:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._subscribers: set[asyncio.Queue[Any]] = set()
        self._closed = False

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Fan an event out to all current subscribers. Non-blocking."""
        if self._closed:
            return
        event = {"session_id": self.session_id, "type": event_type, "data": data}
        for q in self._subscribers:
            q.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events until the consumer stops iterating (SSE closes) OR the bus
        is closed on shutdown. The latter matters: an SSE response is an infinite
        generator, so without an end signal uvicorn's graceful shutdown waits for
        the /events connection to close forever. ``close()`` pushes a sentinel
        that ends this loop so the connection closes and shutdown proceeds."""
        if self._closed:
            return
        q: asyncio.Queue[Any] = asyncio.Queue()
        self._subscribers.add(q)
        try:
            while True:
                item = await q.get()
                if item is _CLOSE:
                    break
                yield item
        finally:
            self._subscribers.discard(q)

    def close(self) -> None:
        """End all SSE subscribers (called on session/app shutdown)."""
        self._closed = True
        for q in list(self._subscribers):
            q.put_nowait(_CLOSE)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
