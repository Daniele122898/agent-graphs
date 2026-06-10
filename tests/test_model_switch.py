"""Regression: switching an agent's model (or other spec) must take effect on
the next run. The RunningAgent caches its built model, so the orchestrator must
rebuild the worker when the spec changed — carrying history forward."""

from __future__ import annotations

from types import SimpleNamespace

from pydantic_ai.messages import TextPart

import backend.main as main_mod
from backend.agent_state import AgentStateStore
from backend.a2a import MessageLog
from backend.models_domain import AgentSpec, Capabilities, GraphNode, TeamGraph
from backend.sessions import SessionManager
from backend.teams import TeamStore
from tests.conftest import make_sequence_model


def _fake_app(conn):
    return SimpleNamespace(
        state=SimpleNamespace(agent_state=AgentStateStore(conn), messages=MessageLog(conn))
    )


async def test_changing_model_rebuilds_worker(conn, fake_clock, repo, monkeypatch):
    # every resolve_model call returns a fresh scripted model (id irrelevant here)
    monkeypatch.setattr(main_mod, "resolve_model", lambda s: make_sequence_model([[TextPart("ok")]]))

    graph = TeamGraph(nodes=[GraphNode(spec=AgentSpec(
        id="lead", name="Lead", is_entry_point=True, model="lmstudio:model-a",
        capabilities=Capabilities.from_level("read")))])
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    session = SessionManager(conn, clock=fake_clock).create_session(
        team_id=team.id, repo_path=repo, graph=graph)
    app = _fake_app(conn)

    ra1 = await main_mod._get_or_create_running(app, session, "lead")
    assert ra1.spec.model == "lmstudio:model-a"

    # unchanged spec → same worker reused
    ra_same = await main_mod._get_or_create_running(app, session, "lead")
    assert ra_same is ra1

    # the user switches the model (mimic _apply_team_graph swapping the graph)
    new_graph = TeamGraph(nodes=[GraphNode(spec=AgentSpec(
        id="lead", name="Lead", is_entry_point=True, model="lmstudio:model-b",
        capabilities=Capabilities.from_level("read")))])
    session.graph = new_graph

    ra2 = await main_mod._get_or_create_running(app, session, "lead")
    assert ra2 is not ra1, "worker was not rebuilt after model change"
    assert ra2.spec.model == "lmstudio:model-b"

    await ra1.stop()
    await ra2.stop()
