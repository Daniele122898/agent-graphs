"""Observability: LM Studio model stats + per-agent usage aggregation.

LM Studio exposes a richer REST API than the OpenAI-compatible one at
``/api/v0/models``, returning exactly the stats worth surfacing: quantization,
``max_context_length`` vs ``loaded_context_length`` (the documented quirk where
the loaded value is often a small default — worth flagging in the UI),
``state`` (loaded/not-loaded), and ``capabilities`` (e.g. ``tool_use``).

Usage (tokens) is aggregated per agent from completed runs; Phase 2 keeps an
in-memory tally per session, surfaced read-only in the Stats tab.
"""

from __future__ import annotations

import httpx

from .models import lmstudio_base_url


def _lmstudio_root() -> str:
    """The LM Studio host root (the REST API lives at ``/api/v0``, not ``/v1``)."""
    base = lmstudio_base_url()
    return base[: -len("/v1")] if base.endswith("/v1") else base.rstrip("/")


async def lmstudio_models() -> list[dict]:
    """Fetch rich model stats from LM Studio. Raises on connection error; the
    endpoint wrapper turns that into a friendly payload."""
    url = f"{_lmstudio_root()}/api/v0/models"
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json().get("data", [])


class UsageTally:
    """A tiny per-(session, agent) token tally, updated as runs complete."""

    def __init__(self) -> None:
        self._by_agent: dict[str, dict[str, int]] = {}

    def add(self, agent_id: str, *, requests: int = 0, input_tokens: int = 0, output_tokens: int = 0) -> None:
        t = self._by_agent.setdefault(agent_id, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
        t["requests"] += requests
        t["input_tokens"] += input_tokens
        t["output_tokens"] += output_tokens

    def get(self, agent_id: str) -> dict[str, int]:
        return dict(self._by_agent.get(agent_id, {"requests": 0, "input_tokens": 0, "output_tokens": 0}))
