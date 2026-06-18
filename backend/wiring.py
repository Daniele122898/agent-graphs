"""Wiring: harness-independent glue behind the HTTP surface.

``api/`` owns the HTTP surface (``main.py`` boots the app); this module owns the
non-trivial glue that is NOT specific to any agent harness — resolving
sessions/specs, validating + syncing team-graph edits, and constructing the
``TaskRunner`` whose effectful steps delegate to ``session.harness``. All
agent-execution logic (running, history, questions, usage, delegation) lives
behind the harness interface (``backend/harness/``); the per-harness
implementations are ``NativeHarness`` and ``OpenCodeHarness``.

``resolve_model`` is re-exported here on purpose: it is the long-standing test
seam (``monkeypatch.setattr(wiring, "resolve_model", ...)``) and the native
harness resolves models through ``wiring.resolve_model`` at call time.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .domain.graph import validate_structure
from .providers.registry import resolve_model  # noqa: F401 — re-exported test seam
from .domain.models import AgentSpec, Capabilities, GraphNode, TeamGraph
from .runtime.sessions import Session
from .runtime.tasks import ReviewVerdict, TaskRunner, run_check


def starter_team_graph() -> TeamGraph:
    """A minimal starter team for a brand-new team: one entry-point 'lead'
    agent, so the team is immediately launchable (a team needs >=1 entry point).
    The user grows it in the editor."""
    lead = AgentSpec(
        id="lead",
        name="Lead",
        persona="You are the lead engineer. You decompose tasks and coordinate the team.",
        is_entry_point=True,
        capabilities=Capabilities.from_level("read-write"),
    )
    return TeamGraph(nodes=[GraphNode(spec=lead, position={"x": 120, "y": 120})], edges=[])


def resolve_session(app: FastAPI, session_id: str | None) -> Session:
    """Resolve a session by id. A session_id is required — there is no implicit
    default; the client always operates on an explicit, launched session."""
    if not session_id:
        raise HTTPException(400, "session_id is required")
    session = app.state.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, f"no session '{session_id}'")
    return session


def find_spec(session: Session, agent_id: str) -> AgentSpec | None:
    for node in session.graph.nodes:
        if node.spec.id == agent_id:
            return node.spec
    return None


def default_entry_point(session: Session) -> str:
    entry = session.graph.entry_points()
    if not entry:
        raise HTTPException(422, "team has no entry-point agent to receive the task")
    return entry[0]


# --- shared "refuse while busy" guards ---------------------------------------
# ONE definition of the rule, reused by every endpoint that mutates state a live
# run depends on (an agent's history, or the session's team/graph). Mutating
# under a run corrupts the conversation the run is building (and can orphan the
# worker). Any NEW session/agent-mutating endpoint MUST guard with one of these
# rather than re-deriving the check (that omission is exactly how the rebind
# endpoint first shipped able to corrupt a running session).

def require_agent_idle(session: Session, agent_id: str) -> None:
    """409 if this agent has a run in flight."""
    if session.harness.is_busy(session, agent_id):
        raise HTTPException(409, f"agent '{agent_id}' is mid-run — stop it first")


def require_session_idle(session: Session) -> None:
    """409 if ANY of the session's agents has a run in flight (session-wide
    mutations like rebind need the whole session quiet, not just one agent)."""
    busy = [n.spec.id for n in session.graph.nodes if session.harness.is_busy(session, n.spec.id)]
    if busy:
        raise HTTPException(409, f"session is busy (running: {', '.join(busy)}) — stop those agents first")


def apply_team_graph(app: FastAPI, team_id: str, graph: TeamGraph) -> dict:
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


def make_task_runner(app: FastAPI, session: Session) -> TaskRunner:
    """Build a TaskRunner whose effectful steps delegate to the session's
    harness: the assigned agent's task run (with the open-todos continuation
    nudge), a reviewer agent with structured ReviewVerdict output, and a shell
    check in the repo root. Harness-agnostic — works for native and opencode."""

    async def run_agent(agent_id: str, prompt: str) -> str:
        return await session.harness.run_for_task(session, agent_id, prompt)

    async def run_reviewer(reviewer_id: str, task_prompt: str, result: str) -> ReviewVerdict:
        return await session.harness.run_reviewer(session, reviewer_id, task_prompt, result)

    return TaskRunner(
        app.state.tasks,
        run_agent=run_agent,
        run_reviewer=run_reviewer,
        run_check=lambda cmd: run_check(cmd, session.repo_root),
        publish=session.bus.publish,
    )
