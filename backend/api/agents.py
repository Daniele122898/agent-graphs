"""Agent endpoints: run/interject, the SSE event stream, the model-context
(history) views + clear/summarize, stop, and the inter-agent message log."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .. import wiring
from ..runtime.streaming import sse_stream
from .schemas import RunRequest


def install(app: FastAPI) -> None:
    @app.get("/events")
    async def events(session_id: str | None = None) -> StreamingResponse:
        session = wiring.resolve_session(app, session_id)
        return StreamingResponse(sse_stream(session.bus), media_type="text/event-stream")

    @app.post("/api/agent/{agent_id}/run")
    async def run_agent(agent_id: str, body: RunRequest, session_id: str | None = None) -> dict:
        """Give a long-lived agent a prompt. Creates+starts the worker on first
        use; thereafter the same worker handles follow-ups with history."""
        session = wiring.resolve_session(app, session_id)
        await session.harness.submit(session, agent_id, body.prompt)
        return {"status": "started", "agent_id": agent_id}

    @app.post("/api/agent/{agent_id}/interject")
    async def interject_agent(agent_id: str, body: RunRequest, session_id: str | None = None) -> dict:
        """Inject a message. If the agent is running, it's processed right after
        the current run (with full history); if idle, it runs now."""
        session = wiring.resolve_session(app, session_id)
        await session.harness.submit(session, agent_id, body.prompt)
        return {"status": "queued", "agent_id": agent_id}

    def _require_not_busy(session, agent_id: str) -> None:
        # Mutating history under a run would corrupt the conversation the run
        # is building.
        if session.harness.is_busy(session, agent_id):
            raise HTTPException(409, "agent is mid-run — stop it first")

    @app.get("/api/agent/{agent_id}/history")
    async def agent_history(agent_id: str, session_id: str | None = None) -> dict:
        """The agent's real model context: the system sections sent with every
        request plus the full stored conversation, rendered as transcript rows."""
        session = wiring.resolve_session(app, session_id)
        return (await session.harness.history(session, agent_id)).payload()

    @app.post("/api/agent/{agent_id}/history/clear")
    async def clear_agent_history(agent_id: str, session_id: str | None = None) -> dict:
        """Wipe the conversation for a fresh start. Instructions (persona,
        capabilities, neighbors, environment) are rebuilt every request, so
        the agent keeps its identity — it just forgets the conversation."""
        session = wiring.resolve_session(app, session_id)
        _require_not_busy(session, agent_id)
        await session.harness.clear_history(session, agent_id)
        return {"status": "cleared", "agent_id": agent_id}

    @app.post("/api/agent/{agent_id}/history/summarize")
    async def summarize_agent_history(agent_id: str, session_id: str | None = None) -> dict:
        """Compact the conversation: ask the agent's model to summarize it,
        then replace the history with just the summary."""
        session = wiring.resolve_session(app, session_id)
        _require_not_busy(session, agent_id)
        try:
            rows = await session.harness.summarize_history(session, agent_id)
        except HTTPException:
            raise  # e.g. 409 "no history to summarize"
        except Exception as e:  # noqa: BLE001 — surface model failures as a clean 502
            raise HTTPException(502, f"summarization failed: {e}")
        return {"status": "summarized", "agent_id": agent_id, "rows": rows}

    @app.post("/api/agent/{agent_id}/stop")
    async def stop_agent(agent_id: str, session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        await session.harness.stop(session, agent_id)
        return {"status": "stopped", "agent_id": agent_id}

    @app.get("/api/messages")
    def messages(session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        return {"messages": app.state.messages.for_session(session.id)}
