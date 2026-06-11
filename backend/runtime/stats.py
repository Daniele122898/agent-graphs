"""Per-agent usage aggregation (tokens/requests), surfaced in the Stats tab.

In-memory and per-session (owned by ``Session``, like the bus and registry).
Model-backend stats (e.g. LM Studio's model list) live in ``providers/``.
"""

from __future__ import annotations


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
