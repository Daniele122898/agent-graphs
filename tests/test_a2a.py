"""Agent-to-agent delegation: neighbor lists, routing, guards, usage, logging.

Driven deterministically with FunctionModel. Failures mean a real behavior is
broken (an agent could consult a non-neighbor, a cycle wasn't refused, an answer
didn't route back), not a cosmetic change.
"""

from __future__ import annotations

from pydantic_ai.messages import TextPart, ToolCallPart
from pydantic_ai.usage import RunUsage

from backend.a2a import Delegator, MessageLog, neighbor_instructions, neighbor_list
from backend.agents import build_agent
from backend.models_domain import AgentSpec, Capabilities, GraphEdge, GraphNode, TeamGraph
from backend.sessions import SessionManager
from backend.teams import TeamStore
from backend.todos import AgentDeps
from backend.tools import DevTools
from tests.conftest import make_sequence_model


def _spec(id_: str, entry: bool = False) -> AgentSpec:
    return AgentSpec(id=id_, name=id_.title(), is_entry_point=entry, capabilities=Capabilities.from_level("read"))


def _graph() -> TeamGraph:
    # lead -> react ("React/JSX questions"); lead -> node
    return TeamGraph(
        nodes=[GraphNode(spec=_spec("lead", entry=True)), GraphNode(spec=_spec("react")), GraphNode(spec=_spec("node"))],
        edges=[
            GraphEdge(id="e1", source="lead", target="react", label="React/JSX questions"),
            GraphEdge(id="e2", source="lead", target="node", label="backend questions"),
        ],
    )


# --- pure neighbor list -----------------------------------------------------


def test_neighbor_list_from_edges():
    g = _graph()
    assert neighbor_list(g, "lead") == [("react", "React/JSX questions"), ("node", "backend questions")]
    assert neighbor_list(g, "react") == []


def test_neighbor_instructions_names_targets_and_why():
    instr = neighbor_instructions(_graph(), "lead")
    assert "`react`" in instr and "React/JSX questions" in instr
    assert "React" in instr  # the human-readable name, not just the id
    assert "ask_agent" in instr
    assert neighbor_instructions(_graph(), "react") == ""  # no neighbors → empty


# --- delegation routing -----------------------------------------------------


def _session(conn, fake_clock, repo, graph):
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    return SessionManager(conn, clock=fake_clock).create_session(team_id=team.id, repo_path=repo, graph=graph)


async def test_ask_routes_to_target_and_returns_answer(conn, fake_clock, repo):
    session = _session(conn, fake_clock, repo, _graph())
    log = MessageLog(conn, clock=fake_clock)

    # The target ('react') answers with plain text.
    def factory(spec):
        model = make_sequence_model([[TextPart(f"answer from {spec.id}")]])
        return build_agent(spec, model=model, dev_tools=DevTools(repo, spec.capabilities))

    delegator = Delegator(session, factory, message_log=log)
    answer = await delegator.ask(
        asker_id="lead", target_id="react", question="how to use hooks?", usage=RunUsage(), chain=[]
    )
    assert answer == "answer from react"
    # logged both the question and the reply
    msgs = log.for_session(session.id)
    assert [(m["from_agent"], m["to_agent"], m["kind"]) for m in msgs] == [
        ("lead", "react", "question"),
        ("react", "lead", "reply"),
    ]


async def test_ask_non_neighbor_is_refused(conn, fake_clock, repo):
    session = _session(conn, fake_clock, repo, _graph())

    def factory(spec):
        return build_agent(spec, model=make_sequence_model([[TextPart("x")]]), dev_tools=DevTools(repo, spec.capabilities))

    delegator = Delegator(session, factory)
    # 'react' is not a neighbor of 'node'
    import pytest
    from pydantic_ai import ModelRetry

    with pytest.raises(ModelRetry, match="not someone you can consult"):
        await delegator.ask(asker_id="node", target_id="react", question="q", usage=RunUsage(), chain=[])


async def test_cycle_guard_refuses_revisit(conn, fake_clock, repo):
    session = _session(conn, fake_clock, repo, _graph())

    def factory(spec):
        return build_agent(spec, model=make_sequence_model([[TextPart("x")]]), dev_tools=DevTools(repo, spec.capabilities))

    delegator = Delegator(session, factory)
    import pytest
    from pydantic_ai import ModelRetry

    # 'react' already in the chain → revisiting it is a cycle
    with pytest.raises(ModelRetry, match="cycle"):
        await delegator.ask(asker_id="lead", target_id="react", question="q", usage=RunUsage(), chain=["react"])


async def test_full_tool_flow_lead_delegates_to_react(conn, fake_clock, repo):
    """End-to-end: the lead's model calls ask_agent('react', …); react answers;
    the lead incorporates it and finishes. Asserts the real delegation path."""
    session = _session(conn, fake_clock, repo, _graph())
    log = MessageLog(conn, clock=fake_clock)

    react_model = make_sequence_model([[TextPart("use useState")]])

    def factory(spec):
        # target agents (react/node) answer in one turn
        return build_agent(spec, model=react_model, dev_tools=DevTools(repo, spec.capabilities))

    delegator = Delegator(session, factory, message_log=log)

    lead_model = make_sequence_model(
        [
            [ToolCallPart("ask_agent", {"target_id": "react", "question": "how to track state?"})],
            [TextPart("Per react: use useState")],
        ]
    )
    lead = build_agent(_spec("lead", entry=True), model=lead_model, dev_tools=DevTools(repo, Capabilities.from_level("read")))
    deps = AgentDeps(session_id=session.id, agent_id="lead", delegator=delegator)
    result = await lead.run("how do I track state in react?", deps=deps)

    assert "useState" in result.output
    msgs = log.for_session(session.id)
    assert any(m["from_agent"] == "lead" and m["to_agent"] == "react" and m["kind"] == "question" for m in msgs)
    assert any(m["from_agent"] == "react" and m["to_agent"] == "lead" and m["kind"] == "reply" for m in msgs)
