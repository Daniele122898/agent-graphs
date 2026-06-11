"""FastAPI application entry point — boot, lifespan, and route mounting.

Boots the persistence stores + ``SessionManager`` and **rehydrates any
previously-persisted sessions** into memory so they survive restarts. It does
*not* auto-create a team or session — teams and sessions are first-class things
the user creates explicitly: define a team (graph + agents), then launch a
session that binds that team to a repo. On a fresh database the app starts
empty and the UI guides you through creating a team and launching a session.

The endpoints live in ``api/`` (one module per resource, registered by
``install_routes``); the non-trivial glue (building/rebuilding RunningAgents,
the TaskRunner's real effect callables, graph sync) lives in ``wiring.py``.
``create_app`` is a factory taking an injected ``db_path`` so tests spin up an
isolated app against a temp DB. Run for real with::

    uvicorn backend.main:app --reload --timeout-graceful-shutdown 3
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agents.a2a import MessageLog
from .api import install_routes
from .runtime.sessions import SessionManager
from .runtime.tasks import TaskStore
from .storage import db as db_module
from .storage.agent_state import AgentStateStore
from .storage.teams import TeamStore


def create_app(*, db_path: str | Path = db_module.DEFAULT_DB_PATH) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db_module.connect(db_path)
        db_module.init_db(conn)
        teams = TeamStore(conn)
        sessions = SessionManager(conn)

        app.state.conn = conn
        app.state.teams = teams
        app.state.sessions = sessions
        app.state.agent_state = AgentStateStore(conn)
        app.state.messages = MessageLog(conn)
        app.state.tasks = TaskStore(conn)
        app.state.task_runs = set()

        # Rehydrate previously-persisted sessions so they survive restarts and
        # show up in the session list. Nothing is auto-created.
        for row in conn.execute("SELECT id, team_id FROM sessions").fetchall():
            team = teams.get(row["team_id"])
            if team is not None:
                sessions.resume_session(row["id"], team.graph)

        # Tasks that were mid-flight when the previous process died have no
        # runner anymore — they'd show "running" forever. Park them in blocked
        # so the user sees they need a nudge (re-create or cancel).
        orphaned = conn.execute(
            "SELECT id FROM tasks WHERE status IN ('running', 'needs_review', 'needs_revision')"
        ).fetchall()
        note = "[orphaned by a server restart — press Retry to run it again]"
        for row in orphaned:
            task = app.state.tasks.get(row["id"])
            prior = task.result.strip() if task else ""
            app.state.tasks.set_result(row["id"], f"{prior}\n\n{note}".strip())
            app.state.tasks.set_status(row["id"], "blocked")

        try:
            yield
        finally:
            for s in sessions.list():
                for ra in s.registry.all_running():
                    await ra.stop()
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

    install_routes(app)
    return app


app = create_app()
