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
