"""Session/SessionManager tests: creation persists, and — critically —
per-session infrastructure is NOT shared (no global singletons)."""

from __future__ import annotations

from backend.domain.models import AgentSpec, GraphNode, TeamGraph
from backend.runtime.sessions import SessionManager
from backend.storage.teams import TeamStore


def _team_with_lead(conn, fake_clock):
    teams = TeamStore(conn, clock=fake_clock)
    graph = TeamGraph(
        nodes=[GraphNode(spec=AgentSpec(id="lead", name="Lead", is_entry_point=True))]
    )
    return teams.create("T", graph)


def test_create_session_persists_row_and_returns_live_session(conn, fake_clock, repo):
    team = _team_with_lead(conn, fake_clock)
    mgr = SessionManager(conn, clock=fake_clock)
    session = mgr.create_session(team_id=team.id, repo_path=repo, graph=team.graph)

    # Row persisted.
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session.id,)).fetchone()
    assert row["team_id"] == team.id
    assert row["repo_path"] == str(repo.resolve())

    # Live session retrievable and its info matches.
    assert mgr.get(session.id) is session
    info = session.info()
    assert info.team_id == team.id
    assert info.mode == "parallel"


def test_registry_seeded_from_graph(conn, fake_clock, repo):
    team = _team_with_lead(conn, fake_clock)
    mgr = SessionManager(conn, clock=fake_clock)
    session = mgr.create_session(team_id=team.id, repo_path=repo, graph=team.graph)
    assert session.registry.agent_ids() == ["lead"]
    assert session.registry.lifecycle("lead") == "idle"


def test_infrastructure_is_per_session_not_global(conn, fake_clock, repo):
    team = _team_with_lead(conn, fake_clock)
    mgr = SessionManager(conn, clock=fake_clock)
    a = mgr.create_session(team_id=team.id, repo_path=repo / "a", graph=team.graph)
    b = mgr.create_session(team_id=team.id, repo_path=repo / "b", graph=team.graph)

    # The whole point: distinct lock/gateway/bus/registry per session.
    assert a.write_lock is not b.write_lock
    assert a.gateway is not b.gateway
    assert a.bus is not b.bus
    assert a.registry is not b.registry
    assert a.bus.session_id != b.bus.session_id


def test_active_sessions_for_repo_flags_collisions(conn, fake_clock, repo):
    team = _team_with_lead(conn, fake_clock)
    mgr = SessionManager(conn, clock=fake_clock)
    s1 = mgr.create_session(team_id=team.id, repo_path=repo, graph=team.graph)
    s2 = mgr.create_session(team_id=team.id, repo_path=repo, graph=team.graph)
    found = mgr.active_sessions_for_repo(repo)
    assert {s.id for s in found} == {s1.id, s2.id}
