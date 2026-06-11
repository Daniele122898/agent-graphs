"""LLM execution gateway — the chokepoint every model call routes through.

This sits *below* the task system and answers a different question: not "what
work exists and its state" (that's tasks.py) but "how do model calls dispatch
against finite compute". On a low-spec machine with one model loaded, model
calls must not overlap; on a capable machine they should run concurrently.

- **parallel** (default): pass-through — calls run concurrently.
- **serial**: an ``asyncio.Semaphore(1)`` admits exactly one in-flight call;
  the rest await their slot.

Owned **per session** (not a global), so a low-power local-model session can
serialize while a hosted-model session runs in parallel.

Phase 0 ships the structurally-correct minimum: ``run()`` wraps any awaitable
and, in serial mode, holds the semaphore for its duration. Phase 6 adds the
"waiting for model slot" event emission and wires every model call through it.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Awaitable, AsyncIterator, Callable, TypeVar

from pydantic_ai.models.wrapper import WrapperModel

from ..domain.models import SessionMode

T = TypeVar("T")


class Gateway:
    def __init__(self, mode: SessionMode = "parallel", *, on_wait: Callable[[], None] | None = None):
        self._mode: SessionMode = mode
        self._sem = asyncio.Semaphore(1)
        self._on_wait = on_wait

    @property
    def mode(self) -> SessionMode:
        return self._mode

    def set_mode(self, mode: SessionMode) -> None:
        self._mode = mode

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold a dispatch slot for the duration of the block. Serial mode admits
        one at a time; parallel mode is a no-op. Emits ``on_wait`` if a caller has
        to queue (so the UI can show "waiting for model slot")."""
        if self._mode != "serial":
            yield
            return
        if self._sem.locked() and self._on_wait is not None:
            self._on_wait()
        async with self._sem:
            yield

    async def run(self, awaitable: Awaitable[T]) -> T:
        """Dispatch a single model call through a slot."""
        async with self.slot():
            return await awaitable


class GatedModel(WrapperModel):
    """A model wrapper that routes every request through a session's gateway, so
    on a low-spec single-model machine all model calls (agent turns, ask_agent
    delegations, reviewer gates, compaction) serialize automatically. In parallel
    mode it's a transparent pass-through."""

    def __init__(self, wrapped, gateway: Gateway):
        super().__init__(wrapped)
        self._gateway = gateway

    async def request(self, messages, model_settings, model_request_parameters):
        async with self._gateway.slot():
            return await self.wrapped.request(messages, model_settings, model_request_parameters)

    @contextlib.asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        # Hold the slot for the whole stream so a streamed call doesn't overlap
        # another model call in serial mode.
        async with self._gateway.slot():
            async with self.wrapped.request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as stream:
                yield stream
