"""The harness abstraction seam: sessions get a harness, the factory selects
it, the shared delegation guards are correct, and the base ``delegate()`` path
(which the OpenCode harness reuses) runs a target end-to-end on the native
harness."""

from __future__ import annotations

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import TextPart

import backend.wiring as wiring
from backend.agents.a2a import MessageLog
from backend.domain.models import AgentSpec, Capabilities, GraphEdge, GraphNode, TeamGraph
from backend.harness import DEFAULT_HARNESS, make_harness
from backend.harness.base import check_delegation
from backend.harness.native import NativeHarness
from backend.runtime.sessions import SessionManager
from backend.storage.agent_state import AgentStateStore
from backend.storage.teams import TeamStore
from tests.conftest import make_sequence_model


def _two_agent_graph() -> TeamGraph:
    lead = AgentSpec(id="lead", name="Lead", is_entry_point=True, model="m:lead",
                     capabilities=Capabilities.from_level("read-write"))
    expert = AgentSpec(id="expert", name="Expert", model="m:expert",
                       capabilities=Capabilities.from_level("read"))
    return TeamGraph(
        nodes=[GraphNode(spec=lead), GraphNode(spec=expert)],
        edges=[GraphEdge(id="e1", source="lead", target="expert", label="naming")],
    )


def _session(conn, fake_clock, repo, graph):
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    return SessionManager(conn, clock=fake_clock).create_session(
        team_id=team.id, repo_path=repo, graph=graph
    )


# --- wiring + factory ---------------------------------------------------------


def test_session_gets_native_harness_by_default(conn, fake_clock, repo):
    session = _session(conn, fake_clock, repo, _two_agent_graph())
    assert isinstance(session.harness, NativeHarness)
    assert session.harness.id == "native"
    assert session.info().harness == "native"
    assert DEFAULT_HARNESS == "native"


def test_make_harness_rejects_unknown():
    with pytest.raises(ValueError, match="unknown harness"):
        make_harness("bogus", state_store=None, message_log=None)


def test_harness_choice_persists_across_resume(conn, fake_clock, repo):
    graph = _two_agent_graph()
    mgr = SessionManager(conn, clock=fake_clock)
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    s = mgr.create_session(team_id=team.id, repo_path=repo, graph=graph, harness="native")
    # a fresh manager (process restart) rehydrates the persisted harness choice
    resumed = SessionManager(conn, clock=fake_clock).resume_session(s.id, graph)
    assert resumed.info().harness == "native"


# --- shared delegation guards (pure) ------------------------------------------


def test_check_delegation_allows_a_neighbor():
    g = _two_agent_graph()
    spec = check_delegation(g, "lead", "expert", [])
    assert spec.id == "expert"


def test_check_delegation_rejects_non_neighbor():
    g = _two_agent_graph()
    with pytest.raises(ModelRetry, match="not someone you can consult"):
        check_delegation(g, "expert", "lead", [])  # edge is lead->expert only


def test_check_delegation_rejects_cycle_and_depth():
    g = _two_agent_graph()
    with pytest.raises(ModelRetry, match="cycle"):
        check_delegation(g, "lead", "expert", ["expert"])
    with pytest.raises(ModelRetry, match="depth cap"):
        check_delegation(g, "lead", "expert", ["a", "b", "c"])


# --- base delegate() runs the target (the path the opencode harness reuses) ---


async def test_native_delegate_runs_target_and_logs(conn, fake_clock, repo, monkeypatch):
    monkeypatch.setattr(
        wiring, "resolve_model", lambda s: make_sequence_model([[TextPart("call it result.txt")]])
    )
    graph = _two_agent_graph()
    session = _session(conn, fake_clock, repo, graph)

    answer = await session.harness.delegate(session, "lead", "expert", "what file name?")
    assert "result.txt" in answer

    # the asker is back to running and the message log captured both directions
    logged = session.harness.message_log.for_session(session.id)
    assert any(m["from_agent"] == "lead" and m["to_agent"] == "expert" and m["kind"] == "question" for m in logged)
    assert any(m["from_agent"] == "expert" and m["to_agent"] == "lead" and m["kind"] == "reply" for m in logged)


async def test_native_delegate_enforces_guards(conn, fake_clock, repo, monkeypatch):
    monkeypatch.setattr(wiring, "resolve_model", lambda s: make_sequence_model([[TextPart("x")]]))
    session = _session(conn, fake_clock, repo, _two_agent_graph())
    with pytest.raises(ModelRetry):
        await session.harness.delegate(session, "expert", "lead", "reverse edge not allowed")
