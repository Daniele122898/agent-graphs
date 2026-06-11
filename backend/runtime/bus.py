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


class EventBus:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Fan an event out to all current subscribers. Non-blocking."""
        event = {"session_id": self.session_id, "type": event_type, "data": data}
        for q in self._subscribers:
            q.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events until the consumer stops iterating (e.g. SSE closes)."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
