"""Observability endpoints: model-backend stats + per-agent usage."""

from __future__ import annotations

from fastapi import FastAPI

from .. import wiring
from ..providers.lmstudio import lmstudio_models


def install(app: FastAPI) -> None:
    @app.get("/api/stats/models")
    async def stats_models() -> dict:
        """LM Studio model stats for the Stats tab + Capabilities model picker.
        Returns a friendly error payload (not a 500) if LM Studio is unreachable,
        so the UI degrades gracefully when no local server is running."""
        try:
            return {"models": await lmstudio_models(), "error": None}
        except Exception as e:  # noqa: BLE001
            return {"models": [], "error": str(e)}

    @app.get("/api/stats/usage/{agent_id}")
    def stats_usage(agent_id: str, session_id: str | None = None) -> dict:
        return wiring.resolve_session(app, session_id).usage.get(agent_id)
