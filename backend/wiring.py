"""Wiring: the glue that turns injected-callable abstractions into real runs.

``main.py`` owns the HTTP surface; this module owns the non-trivial wiring
behind it — resolving sessions/specs, building (and rebuilding) ``RunningAgent``
workers, constructing the ``TaskRunner`` with real effect callables, and syncing
team-graph edits into the bound session. Split out so the orchestration logic is
reviewable and testable apart from the endpoint plumbing.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic_ai import Agent

from .gateway import GatedModel
from .graph import validate_structure
from .models import resolve_model
from .models_domain import AgentSpec, Capabilities, GraphNode, TeamGraph
from .runtime import RunningAgent
from .sessions import Session
from .tasks import ReviewVerdict, TaskRunner, run_check

REVIEW_GUIDANCE = (
    "\n\nYou are acting as a reviewer. Decide whether the result fully satisfies "
    "the task. Approve only if it does; otherwise reject with a concrete, "
    "actionable critique of what is missing or wrong."
)


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
    """Build a TaskRunner whose effectful steps run real agents/checks against
    the session: the assigned agent via its RunningAgent, a reviewer agent with
    structured ReviewVerdict output, and a shell check in the repo root."""

    async def run_agent(agent_id: str, prompt: str) -> str:
        ra = await get_or_create_running(app, session, agent_id)
        return await ra.run_once(prompt)

    async def run_reviewer(reviewer_id: str, task_prompt: str, result: str) -> ReviewVerdict:
        spec = find_spec(session, reviewer_id)
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


async def get_or_create_running(app: FastAPI, session: Session, agent_id: str) -> RunningAgent:
    spec = find_spec(session, agent_id)
    if spec is None:
        raise HTTPException(404, f"no agent '{agent_id}' in this session")

    existing = session.registry.running(agent_id)
    if existing is not None:
        # Reuse the live worker only if its config is unchanged. If the user
        # edited the spec (e.g. switched the model in Capabilities, or changed
        # persona/capabilities), rebuild so the change takes effect on the next
        # run — carrying the conversation history forward.
        if not existing.spec_changed(spec):
            return existing
        prior_messages = list(existing.messages)
        await existing.stop()
        session.registry.detach_running(agent_id)
    else:
        prior_messages = app.state.agent_state.load_messages(session.id, agent_id)

    ra = RunningAgent(
        session=session,
        spec=spec,
        model=resolve_model(spec.model),
        state_store=app.state.agent_state,
        message_log=app.state.messages,
        initial_messages=prior_messages,
    )
    session.registry.attach_running(agent_id, ra)
    ra.start()
    return ra
