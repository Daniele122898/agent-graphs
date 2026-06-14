"""Team library: CRUD + the pin guarantee (editing a *template* never mutates a
running session bound to a different team)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.domain.models import AgentSpec, GraphNode, TeamGraph
from tests.conftest import bootstrap_session


def _graph(lead_name: str = "Lead") -> dict:
    return TeamGraph(
        nodes=[GraphNode(spec=AgentSpec(id="lead", name=lead_name, is_entry_point=True))]
    ).model_dump()


def test_team_crud_round_trip(tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        assert client.get("/api/teams").json()["teams"] == []  # nothing auto-created

        created = client.post("/api/teams", json={"name": "Web Squad", "graph": _graph()}).json()
        assert created["name"] == "Web Squad"

        teams = client.get("/api/teams").json()["teams"]
        assert {t["name"] for t in teams} == {"Web Squad"}

        renamed = client.post(f"/api/teams/{created['id']}/rename", json={"name": "Renamed"}).json()
        assert renamed["name"] == "Renamed"


def test_editing_a_template_does_not_mutate_a_running_session(tmp_path):
    """A session is launched from team A. Editing a *different* template (B) must
    not change what the running session sees (pin-at-launch)."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team_a, session = bootstrap_session(client, tmp_path / "repo", graph=_graph())
        other = client.post("/api/teams", json={"name": "Other", "graph": _graph()}).json()
        big = TeamGraph(
            nodes=[
                GraphNode(spec=AgentSpec(id="lead", name="Lead", is_entry_point=True)),
                GraphNode(spec=AgentSpec(id="extra", name="Extra")),
            ]
        ).model_dump()
        client.put(f"/api/teams/{other['id']}/graph", json=big)

        live = app.state.sessions.get(session["id"])
        assert live.registry.agent_ids() == ["lead"]  # untouched


def test_editing_the_sessions_own_team_syncs_the_session(tmp_path):
    """Editing the team a session is bound to DOES sync it (the editor doubles as
    the live control room for that session)."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team, session = bootstrap_session(client, tmp_path / "repo", graph=_graph())
        big = TeamGraph(
            nodes=[
                GraphNode(spec=AgentSpec(id="lead", name="Lead", is_entry_point=True)),
                GraphNode(spec=AgentSpec(id="react", name="React")),
            ]
        ).model_dump()
        client.put(f"/api/teams/{team['id']}/graph", json=big)
        live = app.state.sessions.get(session["id"])
        assert set(live.registry.agent_ids()) == {"lead", "react"}


def test_create_team_rejects_malformed_graph(tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        bad = TeamGraph(nodes=[GraphNode(spec=AgentSpec(id="a", name="A"))]).model_dump()
        bad["edges"] = [{"id": "e1", "source": "a", "target": "ghost", "label": ""}]
        r = client.post("/api/teams", json={"name": "Bad", "graph": bad})
        assert r.status_code == 422

def test_rebind_session_to_different_team(tmp_path):
    """Rebinding a session to a new team updates its graph and registry."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team_a, session = bootstrap_session(client, tmp_path / "repo", graph=_graph("LeadA"))
        team_b = client.post("/api/teams", json={"name": "Team B", "graph": _graph("LeadB")}).json()
        
        # Rebind session to team B
        r = client.post(f"/api/sessions/{session['id']}/rebind", json={"team_id": team_b["id"]})
        assert r.status_code == 200
        info = r.json()
        assert info["team_id"] == team_b["id"]
        
        # Verify in-memory state
        live = app.state.sessions.get(session["id"])
        assert live.team_id == team_b["id"]
        assert set(live.registry.agent_ids()) == {"lead"}


def test_rebind_session_to_nonexistent_team(tmp_path):
    """Rebinding to a nonexistent team returns 404."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team_a, session = bootstrap_session(client, tmp_path / "repo")
        r = client.post(f"/api/sessions/{session['id']}/rebind", json={"team_id": "nonexistent"})
        assert r.status_code == 404

def test_rebind_session_nonexistent_session(tmp_path):
    """Rebinding a nonexistent session returns 404."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team = client.post("/api/teams", json={"name": "T", "graph": _graph()}).json()
        r = client.post("/api/sessions/nonexistent/rebind", json={"team_id": team["id"]})
        assert r.status_code == 404


def test_rebind_session_registry_updates_for_graph_changes(tmp_path):
    """Rebinding to a team with different agents correctly seeds and detaches."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team_a, session = bootstrap_session(client, tmp_path / "repo", graph=_graph("LeadA"))
        # Team B has lead + an extra agent
        big_graph = TeamGraph(
            nodes=[
                GraphNode(spec=AgentSpec(id="lead", name="LeadB", is_entry_point=True)),
                GraphNode(spec=AgentSpec(id="react", name="React")),
            ]
        ).model_dump()
        team_b = client.post("/api/teams", json={"name": "Team B", "graph": big_graph}).json()
        
        r = client.post(f"/api/sessions/{session['id']}/rebind", json={"team_id": team_b["id"]})
        assert r.status_code == 200
        
        live = app.state.sessions.get(session["id"])
        assert set(live.registry.agent_ids()) == {"lead", "react"}
        
        # Rebind back to a team with only lead — extra agent should be detached
        team_c = client.post("/api/teams", json={"name": "Team C", "graph": _graph("LeadC")}).json()
        r = client.post(f"/api/sessions/{session['id']}/rebind", json={"team_id": team_c["id"]})
        assert r.status_code == 200
        assert set(live.registry.agent_ids()) == {"lead"}
