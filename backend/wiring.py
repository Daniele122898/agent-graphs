"""Wiring: the glue that turns injected-callable abstractions into real runs.

``api/`` owns the HTTP surface (``main.py`` boots the app); this module owns the non-trivial wiring
behind it — resolving sessions/specs, building (and rebuilding) ``RunningAgent``
workers, constructing the ``TaskRunner`` with real effect callables, and syncing
team-graph edits into the bound session. Split out so the orchestration logic is
reviewable and testable apart from the endpoint plumbing.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from .agents.a2a import neighbor_instructions
from .runtime.gateway import GatedModel
from .domain.graph import validate_structure
from .providers.registry import resolve_model, thinking_settings
from .domain.models import AgentSpec, Capabilities, GraphNode, TeamGraph
from .agents.persona import build_instructions, environment_instructions
from .runtime.workers import RunningAgent, obtain_worker
from .runtime.sessions import Session
from .runtime.tasks import ReviewVerdict, TaskRunner, run_check

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


CONTINUATION_NUDGES = 2
"""How many times a task run that stops with unfinished todos gets re-prompted.
Small local models drift into ending their turn mid-plan; the nudge converts
"accidentally stopped" into "kept working" without risking an infinite loop."""


def make_task_runner(app: FastAPI, session: Session) -> TaskRunner:
    """Build a TaskRunner whose effectful steps run real agents/checks against
    the session: the assigned agent via its RunningAgent, a reviewer agent with
    structured ReviewVerdict output, and a shell check in the repo root."""

    async def run_agent(agent_id: str, prompt: str) -> str:
        ra = await get_or_create_running(app, session, agent_id)
        output = await ra.run_once(prompt)
        # Anti-stall: a run that ends while its own checklist has open items
        # either forgot to finish or forgot to update the list — both deserve
        # a nudge rather than silently calling the task complete.
        for _ in range(CONTINUATION_NUDGES):
            open_items = [t for t in ra.todos if t.status != "completed"]
            if not open_items:
                break
            bullet = "\n".join(f"- [{t.status}] {t.content}" for t in open_items)
            output = await ra.run_once(
                "Your run ended but your todo list still has open items:\n"
                f"{bullet}\n\n"
                "Continue working through them now. If an item is genuinely done, "
                "mark it completed via write_todos. If you need the user, call "
                "ask_user. If something blocks you, state exactly what."
            )
        return output

    async def run_reviewer(reviewer_id: str, task_prompt: str, result: str) -> ReviewVerdict:
        spec = find_spec(session, reviewer_id)
        if spec is None:
            return ReviewVerdict(approved=True, critique=f"(no reviewer '{reviewer_id}'; auto-approved)")
        reviewer = Agent(
            model=GatedModel(resolve_model(spec.model), session.gateway),
            output_type=ReviewVerdict,
            instructions=(spec.persona or f"You are {spec.name}.") + REVIEW_GUIDANCE,
            model_settings=thinking_settings(spec.model, spec.thinking, spec.thinking_effort),
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


def agent_context_sections(session: Session, spec: AgentSpec) -> list[str]:
    """The system context an agent's model request carries — static persona/
    capability instructions plus the dynamic fragments (named neighbors, then
    environment last), in the order they are sent. For the control room's
    "what does the model actually see" view."""
    sections = [
        build_instructions(spec),
        neighbor_instructions(session.graph, spec.id),
        environment_instructions(spec, session.repo_root),
    ]
    return [s for s in sections if s]


def agent_messages(app: FastAPI, session: Session, agent_id: str) -> list[ModelMessage]:
    """The agent's current conversation: the live worker's in-memory history if
    one exists, else the persisted snapshot."""
    ra = session.registry.running(agent_id)
    if ra is not None:
        return list(ra.messages)
    return app.state.agent_state.load_messages(session.id, agent_id)


def set_agent_history(app: FastAPI, session: Session, agent_id: str, messages: list[ModelMessage]) -> None:
    """Replace the agent's conversation (clear / summarize-compact), keeping the
    live worker and the persisted snapshot in agreement."""
    ra = session.registry.running(agent_id)
    if ra is not None:
        ra.replace_history(messages)  # persists via the worker
    else:
        app.state.agent_state.save(
            session.id,
            agent_id,
            messages=messages,
            lifecycle=session.registry.lifecycle(agent_id) or "idle",
            usage=session.usage.get(agent_id),
        )


SUMMARIZE_PROMPT = (
    "Summarize this entire conversation into a compact briefing for yourself: "
    "what was asked, what you did (files created or changed, key decisions, "
    "results), and any open follow-ups. Write it so you could continue the "
    "work from the summary alone. Reply with ONLY the summary."
)


async def summarize_agent_history(
    session: Session, spec: AgentSpec, messages: list[ModelMessage]
) -> list[ModelMessage]:
    """Compress an agent's conversation: one model call to summarize it, then a
    fresh two-message history carrying just the summary. Instructions are sticky
    (rebuilt every request), so the persona/capability context is unaffected."""
    summarizer = Agent(
        model=GatedModel(resolve_model(spec.model), session.gateway),
        instructions=spec.persona or f"You are {spec.name}.",
        model_settings=thinking_settings(spec.model, spec.thinking, spec.thinking_effort),
    )
    r = await summarizer.run(SUMMARIZE_PROMPT, message_history=messages)
    summary = str(r.output).strip()
    return [
        ModelRequest(parts=[UserPromptPart(content=(
            "[Conversation compacted — summary of all prior work]\n\n" + summary
        ))]),
        ModelResponse(parts=[TextPart(content="Understood — I'll continue from this summary.")]),
    ]


async def get_or_create_running(app: FastAPI, session: Session, agent_id: str) -> RunningAgent:
    """HTTP-facing get-or-create: resolve the spec, then defer to the shared
    ``obtain_worker`` path (also used by delegation), which reuses a live worker
    unless its spec changed and otherwise rebuilds carrying history forward."""
    spec = find_spec(session, agent_id)
    if spec is None:
        raise HTTPException(404, f"no agent '{agent_id}' in this session")
    return await obtain_worker(
        session,
        spec,
        state_store=app.state.agent_state,
        message_log=app.state.messages,
        model_resolver=resolve_model,
    )
