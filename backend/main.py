"""FastAPI application entry point — the HTTP/SSE surface.

Boots the persistence stores + ``SessionManager`` and **rehydrates any
previously-persisted sessions** into memory so they survive restarts. It does
*not* auto-create a team or session — teams and sessions are first-class things
the user creates explicitly: define a team (graph + agents), then launch a
session that binds that team to a repo. On a fresh database the app starts
empty and the UI guides you through creating a team and launching a session.

The non-trivial glue (building/rebuilding RunningAgents, the TaskRunner's real
effect callables, graph sync) lives in ``wiring.py``; request bodies in
``schemas.py``. ``create_app`` is a factory taking an injected ``db_path`` so
tests spin up an isolated app against a temp DB. Run for real with::

    uvicorn backend.main:app --reload
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import db as db_module
from . import wiring
from .a2a import MessageLog
from .agent_state import AgentStateStore
from .graph import validate_structure
from .models_domain import TeamGraph
from .schemas import (
    LaunchSessionRequest,
    ModeRequest,
    NewTaskRequest,
    NewTeamRequest,
    RenameRequest,
    RunRequest,
)
from .sessions import SessionManager
from .stats import lmstudio_models
from .streaming import sse_stream
from .tasks import TaskStore
from .teams import TeamStore


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
        note = "[orphaned by a server restart — re-create the task to retry]"
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

    # --- sessions ------------------------------------------------------------

    @app.get("/api/session")
    def current_session(session_id: str) -> dict:
        return wiring.resolve_session(app, session_id).info().model_dump()

    @app.get("/api/sessions")
    def list_sessions() -> dict:
        return {"sessions": [s.info().model_dump() for s in app.state.sessions.list()]}

    @app.post("/api/sessions")
    def launch_session(body: LaunchSessionRequest) -> dict:
        """Launch a new session: bind a team definition to a repo. Warns (does
        not block) if another active session already binds that repo — two task
        forces will fight over the same files."""
        team = app.state.teams.get(body.team_id)
        if team is None:
            raise HTTPException(404, f"no team '{body.team_id}'")
        existing = app.state.sessions.active_sessions_for_repo(body.repo_path)
        Path(body.repo_path).mkdir(parents=True, exist_ok=True)
        session = app.state.sessions.create_session(
            team_id=team.id, repo_path=body.repo_path, graph=team.graph, mode=body.mode
        )
        info = session.info().model_dump()
        info["warning"] = (
            f"{len(existing)} other active session(s) already bound to this repo"
            if existing else None
        )
        return info

    @app.post("/api/sessions/{session_id}/resume")
    def resume_session(session_id: str) -> dict:
        """Rehydrate a persisted session into memory (snapshot/resume). The team
        graph is reloaded from its definition; per-agent history reloads lazily
        when each agent next runs."""
        live = app.state.sessions.get(session_id)
        if live is not None:
            return live.info().model_dump()
        row = app.state.conn.execute(
            "SELECT team_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "no such session")
        team = app.state.teams.get(row["team_id"])
        if team is None:
            raise HTTPException(409, f"team '{row['team_id']}' no longer exists; session cannot be resumed")
        session = app.state.sessions.resume_session(session_id, team.graph)
        if session is None:
            raise HTTPException(404, "no such session")
        return session.info().model_dump()

    @app.post("/api/session/mode")
    def set_mode(body: ModeRequest, session_id: str) -> dict:
        """Toggle the LLM execution gateway mode for this session: parallel
        (default) or serial (low-spec, one model call at a time)."""
        session = wiring.resolve_session(app, session_id)
        session.gateway.set_mode(body.mode)
        return session.info().model_dump()

    # --- team library ----------------------------------------------------------

    @app.get("/api/teams")
    def list_teams() -> dict:
        return {"teams": [t.model_dump() for t in app.state.teams.list()]}

    @app.post("/api/teams")
    def create_team(body: NewTeamRequest) -> dict:
        # A brand-new team gets a starter lead agent (so it's launchable) unless
        # an explicit graph was supplied (e.g. "save as" from the editor).
        graph = body.graph if body.graph is not None else wiring.starter_team_graph()
        errors = validate_structure(graph)
        if errors:
            raise HTTPException(422, {"errors": errors})
        return app.state.teams.create(body.name, graph).model_dump()

    @app.get("/api/teams/{team_id}")
    def get_team(team_id: str) -> dict:
        team = app.state.teams.get(team_id)
        if team is None:
            raise HTTPException(404, "no such team")
        return team.model_dump()

    @app.get("/api/teams/{team_id}/graph")
    def get_team_graph(team_id: str) -> dict:
        team = app.state.teams.get(team_id)
        if team is None:
            raise HTTPException(404, "no such team")
        return team.graph.model_dump()

    @app.put("/api/teams/{team_id}/graph")
    def put_team_graph(team_id: str, graph: TeamGraph) -> dict:
        return wiring.apply_team_graph(app, team_id, graph)

    @app.post("/api/teams/{team_id}/rename")
    def rename_team(team_id: str, body: RenameRequest) -> dict:
        team = app.state.teams.rename(team_id, body.name)
        if team is None:
            raise HTTPException(404, "no such team")
        return team.model_dump()

    # --- agents + events -------------------------------------------------------

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

    @app.post("/api/agent/{agent_id}/stop")
    async def stop_agent(agent_id: str, session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        ra = session.registry.running(agent_id)
        if ra is not None:
            await ra.stop()
            session.registry.detach_running(agent_id)  # allow a fresh start
        return {"status": "stopped", "agent_id": agent_id}

    # --- stats + messages --------------------------------------------------------

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

    @app.get("/api/messages")
    def messages(session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        return {"messages": app.state.messages.for_session(session.id)}

    # --- tasks -------------------------------------------------------------------

    @app.get("/api/tasks")
    def list_tasks(session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        return {"tasks": [t.model_dump() for t in app.state.tasks.list_for_session(session.id)]}

    @app.post("/api/tasks")
    async def create_task(body: NewTaskRequest, session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        agent_id = body.assigned_agent_id or wiring.default_entry_point(session)
        if wiring.find_spec(session, agent_id) is None:
            raise HTTPException(404, f"no agent '{agent_id}' to assign the task to")
        task = app.state.tasks.create(
            session_id=session.id,
            title=body.title or body.prompt[:60],
            prompt=body.prompt,
            assigned_agent_id=agent_id,
            completion_signal=body.completion_signal,
        )
        runner = wiring.make_task_runner(app, session)
        t = asyncio.create_task(runner.run(task.id))
        app.state.task_runs.add(t)
        t.add_done_callback(app.state.task_runs.discard)
        return task.model_dump()

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        task = app.state.tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "no such task")
        return task.model_dump()

    return app


app = create_app()
