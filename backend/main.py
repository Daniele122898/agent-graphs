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

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic_ai import Agent

from . import db as db_module
from .a2a import MessageLog
from .agent_state import AgentStateStore
from .gateway import GatedModel
from .graph import validate_structure
from .models import resolve_model
from .models_domain import AgentSpec, Capabilities, GraphNode, SessionMode, TeamGraph
from .runtime import RunningAgent
from .sessions import Session, SessionManager
from .stats import lmstudio_models
from .streaming import sse_stream
from .tasks import ReviewVerdict, TaskRunner, TaskStore, run_check
from .teams import TeamStore

REVIEW_GUIDANCE = (
    "\n\nYou are acting as a reviewer. Decide whether the result fully satisfies "
    "the task. Approve only if it does; otherwise reject with a concrete, "
    "actionable critique of what is missing or wrong."
)


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
        app.state.tasks = TaskStore(conn)
        app.state.task_runs = set()
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

    @app.post("/api/session/mode")
    def set_mode(body: ModeRequest) -> dict:
        """Toggle the LLM execution gateway mode for this session: parallel
        (default) or serial (low-spec, one model call at a time)."""
        session = _default_session(app)
        session.gateway.set_mode(body.mode)
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
        return _apply_team_graph(app, app.state.default_team_id, graph)

    # --- team library (Phase 7) --------------------------------------------

    @app.get("/api/teams")
    def list_teams() -> dict:
        return {"teams": [t.model_dump() for t in app.state.teams.list()]}

    @app.post("/api/teams")
    def create_team(body: NewTeamRequest) -> dict:
        graph = body.graph or TeamGraph()
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
        return _apply_team_graph(app, team_id, graph)

    @app.post("/api/teams/{team_id}/rename")
    def rename_team(team_id: str, body: RenameRequest) -> dict:
        team = app.state.teams.rename(team_id, body.name)
        if team is None:
            raise HTTPException(404, "no such team")
        return team.model_dump()

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

    @app.get("/api/tasks")
    def list_tasks() -> dict:
        session = _default_session(app)
        return {"tasks": [t.model_dump() for t in app.state.tasks.list_for_session(session.id)]}

    @app.post("/api/tasks")
    async def create_task(body: NewTaskRequest) -> dict:
        session = _default_session(app)
        agent_id = body.assigned_agent_id or _default_entry_point(session)
        if _find_spec(session, agent_id) is None:
            raise HTTPException(404, f"no agent '{agent_id}' to assign the task to")
        task = app.state.tasks.create(
            session_id=session.id,
            title=body.title or body.prompt[:60],
            prompt=body.prompt,
            assigned_agent_id=agent_id,
            completion_signal=body.completion_signal,
        )
        runner = _make_task_runner(app, session)
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


class NewTaskRequest(BaseModel):
    prompt: str
    title: str = ""
    assigned_agent_id: str | None = None
    completion_signal: str = "self_reported"


class NewTeamRequest(BaseModel):
    name: str
    graph: TeamGraph | None = None


class RenameRequest(BaseModel):
    name: str


def _apply_team_graph(app: FastAPI, team_id: str, graph: TeamGraph) -> dict:
    """Validate + persist a team's graph. If the team is the one a running
    session is currently bound to, also sync the session's *pinned* graph so the
    editor doubles as the live control room for that session. Editing any *other*
    team (a template in the library) never mutates a running session — that's the
    pin-at-launch guarantee.
    """
    errors = validate_structure(graph)
    if errors:
        raise HTTPException(422, {"errors": errors})
    team = app.state.teams.update_graph(team_id, graph)
    if team is None:
        raise HTTPException(404, "no such team")
    for session in app.state.sessions.list():
        if session.team_id == team_id:
            session.graph = team.graph
            for node in team.graph.nodes:
                if session.registry.lifecycle(node.spec.id) is None:
                    session.registry.register(node.spec.id, "idle")
    return team.graph.model_dump()


class ModeRequest(BaseModel):
    mode: SessionMode


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


def _default_entry_point(session: Session) -> str:
    entry = session.graph.entry_points()
    if not entry:
        raise HTTPException(422, "team has no entry-point agent to receive the task")
    return entry[0]


def _make_task_runner(app: FastAPI, session: Session) -> TaskRunner:
    """Build a TaskRunner whose effectful steps run real agents/checks against
    the session: the assigned agent via its RunningAgent, a reviewer agent with
    structured ReviewVerdict output, and a shell check in the repo root."""

    async def run_agent(agent_id: str, prompt: str) -> str:
        return await _get_or_create_running(app, agent_id).run_once(prompt)

    async def run_reviewer(reviewer_id: str, task_prompt: str, result: str) -> ReviewVerdict:
        spec = _find_spec(session, reviewer_id)
        if spec is None:
            return ReviewVerdict(approved=True, critique=f"(no reviewer '{reviewer_id}'; auto-approved)")
        reviewer = Agent(
            model=GatedModel(resolve_model(spec.model), session.gateway),
            output_type=ReviewVerdict,
            instructions=(spec.persona or f"You are {spec.name}.") + REVIEW_GUIDANCE,
        )
        r = await reviewer.run(f"Task:\n{task_prompt}\n\nResult to review:\n{result}")
        return r.output

    return TaskRunner(
        app.state.tasks,
        run_agent=run_agent,
        run_reviewer=run_reviewer,
        run_check=lambda cmd: run_check(cmd, session.repo_root),
        publish=session.bus.publish,
    )


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
