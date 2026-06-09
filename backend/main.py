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

from . import db as db_module
from .models_domain import AgentSpec, Capabilities, GraphNode, TeamGraph
from .sessions import SessionManager
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
        app.state.default_team_id = team.id
        app.state.default_session_id = session.id
        try:
            yield
        finally:
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

    return app


app = create_app()
