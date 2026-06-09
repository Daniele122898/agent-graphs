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
from typing import Awaitable, TypeVar

from .models_domain import SessionMode

T = TypeVar("T")


class Gateway:
    def __init__(self, mode: SessionMode = "parallel"):
        self._mode: SessionMode = mode
        self._sem = asyncio.Semaphore(1)

    @property
    def mode(self) -> SessionMode:
        return self._mode

    def set_mode(self, mode: SessionMode) -> None:
        self._mode = mode

    async def run(self, awaitable: Awaitable[T]) -> T:
        """Dispatch a model call. Serial mode admits one at a time."""
        if self._mode == "serial":
            async with self._sem:
                return await awaitable
        return await awaitable
