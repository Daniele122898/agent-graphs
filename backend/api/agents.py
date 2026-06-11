"""Agent endpoints: run/interject, the SSE event stream, the model-context
(history) views + clear/summarize, stop, and the inter-agent message log."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .. import wiring
from ..agents.history import render_messages
from ..runtime.streaming import sse_stream
from .schemas import RunRequest


def install(app: FastAPI) -> None:
    @app.get("/events")
    async def events(session_id: str | None = None) -> StreamingResponse:
        session = wiring.resolve_session(app, session_id)
        return StreamingResponse(sse_stream(session.bus), media_type="text/event-stream")

    @app.post("/api/agent/{agent_id}/run")
    async def run_agent(agent_id: str, body: RunRequest, session_id: str | None = None) -> dict:
        """Give a long-lived agent a prompt. Creates+starts the RunningAgent on
        first use; thereafter the same worker handles follow-ups with history."""
        session = wiring.resolve_session(app, session_id)
        ra = await wiring.get_or_create_running(app, session, agent_id)
        ra.submit(body.prompt)
        return {"status": "started", "agent_id": agent_id}

    @app.post("/api/agent/{agent_id}/interject")
    async def interject_agent(agent_id: str, body: RunRequest, session_id: str | None = None) -> dict:
        """Inject a message. If the agent is running, it's processed right after
        the current run (with full history); if idle, it runs now."""
        session = wiring.resolve_session(app, session_id)
        ra = await wiring.get_or_create_running(app, session, agent_id)
        ra.submit(body.prompt)
        return {"status": "queued", "agent_id": agent_id}

    def _agent_for_history(session_id: str | None, agent_id: str):
        """Shared resolution for the history endpoints: session + spec, and a
        409 if the agent is mid-run (mutating history under a run would corrupt
        the conversation the run is building)."""
        session = wiring.resolve_session(app, session_id)
        spec = wiring.find_spec(session, agent_id)
        if spec is None:
            raise HTTPException(404, f"no agent '{agent_id}' in this session")
        return session, spec

    @app.get("/api/agent/{agent_id}/history")
    def agent_history(agent_id: str, session_id: str | None = None) -> dict:
        """The agent's real model context: the system sections sent with every
        request plus the full stored conversation, rendered as transcript rows."""
        session, spec = _agent_for_history(session_id, agent_id)
        msgs = wiring.agent_messages(app, session, agent_id)
        return {
            "instructions": wiring.agent_context_sections(session, spec),
            "rows": render_messages(msgs),
            "message_count": len(msgs),
        }

    def _require_not_busy(session, agent_id: str) -> None:
        ra = session.registry.running(agent_id)
        if ra is not None and ra.busy:
            raise HTTPException(409, "agent is mid-run — stop it first")

    @app.post("/api/agent/{agent_id}/history/clear")
    def clear_agent_history(agent_id: str, session_id: str | None = None) -> dict:
        """Wipe the conversation for a fresh start. Instructions (persona,
        capabilities, neighbors, environment) are rebuilt every request, so
        the agent keeps its identity — it just forgets the conversation."""
        session, _spec = _agent_for_history(session_id, agent_id)
        _require_not_busy(session, agent_id)
        wiring.set_agent_history(app, session, agent_id, [])
        return {"status": "cleared", "agent_id": agent_id}

    @app.post("/api/agent/{agent_id}/history/summarize")
    async def summarize_agent_history(agent_id: str, session_id: str | None = None) -> dict:
        """Compact the conversation: ask the agent's model to summarize it,
        then replace the history with just the summary."""
        session, spec = _agent_for_history(session_id, agent_id)
        _require_not_busy(session, agent_id)
        msgs = wiring.agent_messages(app, session, agent_id)
        if not msgs:
            raise HTTPException(409, "no history to summarize")
        try:
            new_history = await wiring.summarize_agent_history(session, spec, msgs)
        except Exception as e:  # noqa: BLE001 — surface model failures as a clean 502
            raise HTTPException(502, f"summarization failed: {e}")
        wiring.set_agent_history(app, session, agent_id, new_history)
        return {"status": "summarized", "agent_id": agent_id, "rows": render_messages(new_history)}

    @app.post("/api/agent/{agent_id}/stop")
    async def stop_agent(agent_id: str, session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        ra = session.registry.running(agent_id)
        if ra is not None:
            await ra.stop()
            session.registry.detach_running(agent_id)  # allow a fresh start
        return {"status": "stopped", "agent_id": agent_id}

    @app.get("/api/messages")
    def messages(session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        return {"messages": app.state.messages.for_session(session.id)}
