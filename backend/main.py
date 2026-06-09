"""FastAPI application entry point.

Boots the ``SessionManager`` and, in the spirit of the MVP, auto-creates exactly
one team + one session on startup and never surfaces the concept in the API
shape beyond a ``/api/session`` that returns "the current one". The four-table
data model underneath carries ``team_id``/``session_id`` regardless, so going
multi-session later is UI work, not a rewrite.

``create_app`` is a factory taking injected ``db_path`` and ``repo_path`` so
tests spin up an isolated app against a temp DB and temp repo. Run for real with::

    uvicorn backend.main:app --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import db as db_module
from .a2a import MessageLog
from .agent_state import AgentStateStore
from .graph import validate_structure
from .models import resolve_model
from .models_domain import AgentSpec, Capabilities, GraphNode, TeamGraph
from .runtime import RunningAgent
from .sessions import Session, SessionManager
from .stats import lmstudio_models
from .streaming import sse_stream
from .teams import TeamStore


def _default_repo_path() -> Path:
    """Where the auto-created session's agents work. Override with
    ``AGENT_GRAPHS_REPO``; defaults to a gitignored ``workspace/`` so agents
    have a real folder to edit out of the box."""
    env = os.environ.get("AGENT_GRAPHS_REPO")
    if env:
        return Path(env).resolve()
    return (Path(__file__).parent.parent / "workspace").resolve()


def _default_team_graph() -> TeamGraph:
    """A minimal starter team: one entry-point 'lead' agent. The graph editor
    (Phase 1) lets the user grow this; a team needs >=1 entry point."""
    lead = AgentSpec(
        id="lead",
        name="Lead",
        persona="You are the lead engineer. You decompose tasks and coordinate the team.",
        is_entry_point=True,
        capabilities=Capabilities.from_level("read-write"),
    )
    return TeamGraph(nodes=[GraphNode(spec=lead, position={"x": 0, "y": 0})], edges=[])


def create_app(
    *,
    db_path: str | Path = db_module.DEFAULT_DB_PATH,
    repo_path: str | Path | None = None,
) -> FastAPI:
    repo_path = Path(repo_path) if repo_path else _default_repo_path()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db_module.connect(db_path)
        db_module.init_db(conn)
        teams = TeamStore(conn)
        sessions = SessionManager(conn)

        # Auto-create one team + one session (concept hidden in the MVP UI).
        repo_path.mkdir(parents=True, exist_ok=True)
        team = teams.create("Default Team", _default_team_graph())
        session = sessions.create_session(
            team_id=team.id, repo_path=repo_path, graph=team.graph
        )

        app.state.conn = conn
        app.state.teams = teams
        app.state.sessions = sessions
        app.state.agent_state = AgentStateStore(conn)
        app.state.messages = MessageLog(conn)
        app.state.default_team_id = team.id
        app.state.default_session_id = session.id
        try:
            yield
        finally:
            for s in sessions.list():
                for ra in s.registry.all_running():
                    await ra.stop()  # type: ignore[attr-defined]
            conn.close()

    app = FastAPI(title="Agent Graphs", lifespan=lifespan)

    # Local dev only — the Vite dev server runs on a different port.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        tables = db_module.table_names(app.state.conn)
        return {
            "status": "ok",
            "tables": sorted(tables),
            "sessions": len(app.state.sessions.list()),
        }

    @app.get("/api/session")
    def current_session() -> dict:
        session = app.state.sessions.get(app.state.default_session_id)
        if session is None:
            raise HTTPException(500, "no default session")
        return session.info().model_dump()

    @app.get("/api/team")
    def current_team() -> dict:
        team = app.state.teams.get(app.state.default_team_id)
        if team is None:
            raise HTTPException(500, "no default team")
        return team.model_dump()

    @app.get("/api/team/graph")
    def get_graph() -> dict:
        team = app.state.teams.get(app.state.default_team_id)
        if team is None:
            raise HTTPException(500, "no default team")
        return team.graph.model_dump()

    @app.put("/api/team/graph")
    def put_graph(graph: TeamGraph) -> dict:
        errors = validate_structure(graph)
        if errors:
            raise HTTPException(422, {"errors": errors})
        team = app.state.teams.update_graph(app.state.default_team_id, graph)
        if team is None:
            raise HTTPException(500, "no default team")
        # Reflect the edited graph into the running session so a freshly-added
        # agent can be run immediately. (A session normally pins its definition
        # at launch; in the single-session MVP the editor and the running
        # session are the same team, so we keep them in sync. Phase 7/8 make the
        # pin-vs-edit distinction explicit.)
        session = _default_session(app)
        session.graph = team.graph
        for node in team.graph.nodes:
            if session.registry.lifecycle(node.spec.id) is None:
                session.registry.register(node.spec.id, "idle")
        return team.graph.model_dump()

    @app.get("/events")
    async def events() -> StreamingResponse:
        session = _default_session(app)
        return StreamingResponse(sse_stream(session.bus), media_type="text/event-stream")

    @app.post("/api/agent/{agent_id}/run")
    async def run_agent(agent_id: str, body: RunRequest) -> dict:
        """Give a long-lived agent a prompt. Creates+starts the RunningAgent on
        first use; thereafter the same worker handles follow-ups with history."""
        ra = _get_or_create_running(app, agent_id)
        ra.submit(body.prompt)
        return {"status": "started", "agent_id": agent_id}

    @app.post("/api/agent/{agent_id}/interject")
    async def interject_agent(agent_id: str, body: RunRequest) -> dict:
        """Inject a message. If the agent is running, it's processed right after
        the current run (with full history); if idle, it runs now."""
        ra = _get_or_create_running(app, agent_id)
        ra.submit(body.prompt)
        return {"status": "queued", "agent_id": agent_id}

    @app.post("/api/agent/{agent_id}/stop")
    async def stop_agent(agent_id: str) -> dict:
        session = _default_session(app)
        ra = session.registry.running(agent_id)
        if ra is not None:
            await ra.stop()  # type: ignore[attr-defined]
            session.registry.detach_running(agent_id)  # allow a fresh start
        return {"status": "stopped", "agent_id": agent_id}

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
    def stats_usage(agent_id: str) -> dict:
        return _default_session(app).usage.get(agent_id)

    @app.get("/api/messages")
    def messages() -> dict:
        session = _default_session(app)
        return {"messages": app.state.messages.for_session(session.id)}

    return app


class RunRequest(BaseModel):
    prompt: str


def _default_session(app: FastAPI) -> Session:
    session = app.state.sessions.get(app.state.default_session_id)
    if session is None:
        raise HTTPException(500, "no default session")
    return session


def _find_spec(session: Session, agent_id: str) -> AgentSpec | None:
    for node in session.graph.nodes:
        if node.spec.id == agent_id:
            return node.spec
    return None


def _get_or_create_running(app: FastAPI, agent_id: str) -> RunningAgent:
    session = _default_session(app)
    existing = session.registry.running(agent_id)
    if existing is not None:
        return existing  # type: ignore[return-value]
    spec = _find_spec(session, agent_id)
    if spec is None:
        raise HTTPException(404, f"no agent '{agent_id}' in this session")
    ra = RunningAgent(
        session=session,
        spec=spec,
        model=resolve_model(spec.model),
        state_store=app.state.agent_state,
        message_log=app.state.messages,
    )
    session.registry.attach_running(agent_id, ra)
    ra.start()
    return ra


app = create_app()
