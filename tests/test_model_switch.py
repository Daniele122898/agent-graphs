"""Regression: switching an agent's model (or other spec) must take effect on
the next run. The RunningAgent caches its built model, so the orchestrator must
rebuild the worker when the spec changed — carrying history forward."""

from __future__ import annotations

from pydantic_ai.messages import TextPart

import backend.wiring as wiring
from backend.domain.models import AgentSpec, Capabilities, GraphNode, TeamGraph
from backend.runtime.sessions import SessionManager
from backend.storage.teams import TeamStore
from tests.conftest import make_sequence_model


async def test_changing_model_rebuilds_worker(conn, fake_clock, repo, monkeypatch):
    # every resolve_model call returns a fresh scripted model (id irrelevant
    # here). The native harness resolves via wiring.resolve_model at call time.
    monkeypatch.setattr(wiring, "resolve_model", lambda s: make_sequence_model([[TextPart("ok")]]))

    graph = TeamGraph(nodes=[GraphNode(spec=AgentSpec(
        id="lead", name="Lead", is_entry_point=True, model="lmstudio:model-a",
        capabilities=Capabilities.from_level("read")))])
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    session = SessionManager(conn, clock=fake_clock).create_session(
        team_id=team.id, repo_path=repo, graph=graph)
    harness = session.harness  # the NativeHarness; _worker is the get-or-create path

    ra1 = await harness._worker(session, "lead")
    assert ra1.spec.model == "lmstudio:model-a"

    # unchanged spec → same worker reused
    ra_same = await harness._worker(session, "lead")
    assert ra_same is ra1

    # the user switches the model (mimic _apply_team_graph swapping the graph)
    new_graph = TeamGraph(nodes=[GraphNode(spec=AgentSpec(
        id="lead", name="Lead", is_entry_point=True, model="lmstudio:model-b",
        capabilities=Capabilities.from_level("read")))])
    session.graph = new_graph

    ra2 = await harness._worker(session, "lead")
    assert ra2 is not ra1, "worker was not rebuilt after model change"
    assert ra2.spec.model == "lmstudio:model-b"

    await ra1.stop()
    await ra2.stop()


async def test_history_carries_on_model_change_but_resets_on_rename(conn, fake_clock, repo, monkeypatch):
    # Identity for history carry-forward is (id, name). obtain_worker is the one
    # get-or-create choke point, so an in-place rename of an agent in the running
    # team (PUT /teams/{id}/graph) lands here too — the old role's transcript must
    # NOT bleed into the new role; a pure model/persona change (same name) keeps it.
    from backend.storage.agent_state import AgentStateStore
    monkeypatch.setattr(wiring, "resolve_model", lambda s: make_sequence_model([[TextPart("ok")]]))
    graph = TeamGraph(nodes=[GraphNode(spec=AgentSpec(
        id="lead", name="Implementer", is_entry_point=True, model="lmstudio:m1",
        capabilities=Capabilities.from_level("read")))])
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    session = SessionManager(conn, clock=fake_clock).create_session(
        team_id=team.id, repo_path=repo, graph=graph)
    harness = session.harness

    ra1 = await harness._worker(session, "lead")
    await ra1.run_once("do the thing")            # give the slot real history
    assert len(ra1.messages) > 0

    def swap(name: str, model: str):
        session.graph = TeamGraph(nodes=[GraphNode(spec=AgentSpec(
            id="lead", name=name, is_entry_point=True, model=model,
            capabilities=Capabilities.from_level("read")))])

    # MODEL change, SAME name → same agent evolving → history carried
    swap("Implementer", "lmstudio:m2")
    ra2 = await harness._worker(session, "lead")
    assert ra2 is not ra1 and len(ra2.messages) > 0, "same role: history must carry across a model change"

    # RENAME (different role), same id → repurposed slot → history reset
    swap("Frontend Expert", "lmstudio:m2")
    ra3 = await harness._worker(session, "lead")
    assert ra3 is not ra2 and ra3.messages == [], "renamed slot must NOT carry the old role's history"
    # the persisted row was wiped too, so a later reload can't resurrect it
    assert AgentStateStore(conn, clock=fake_clock).load_messages(session.id, "lead") == []

    await ra1.stop()
    await ra2.stop()
    await ra3.stop()
