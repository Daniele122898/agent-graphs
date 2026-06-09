"""Team library: CRUD + the pin guarantee (editing a *template* never mutates a
running session bound to a different team)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.models_domain import AgentSpec, GraphNode, TeamGraph


def _graph(lead_name: str = "Lead") -> dict:
    return TeamGraph(
        nodes=[GraphNode(spec=AgentSpec(id="lead", name=lead_name, is_entry_point=True))]
    ).model_dump()


def test_team_crud_round_trip(tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite", repo_path=tmp_path / "repo")
    with TestClient(app) as client:
        # the auto-created default team is listed
        teams = client.get("/api/teams").json()["teams"]
        assert len(teams) == 1

        created = client.post("/api/teams", json={"name": "Web Squad", "graph": _graph()}).json()
        assert created["name"] == "Web Squad"

        teams = client.get("/api/teams").json()["teams"]
        assert {t["name"] for t in teams} == {"Default Team", "Web Squad"}

        renamed = client.post(f"/api/teams/{created['id']}/rename", json={"name": "Renamed"}).json()
        assert renamed["name"] == "Renamed"


def test_editing_a_template_does_not_mutate_a_running_session(tmp_path):
    """The session is bound to the default team. Editing a *different* saved team
    must not change what the running session sees (pin-at-launch)."""
    app = create_app(db_path=tmp_path / "t.sqlite", repo_path=tmp_path / "repo")
    with TestClient(app) as client:
        # a separate template
        other = client.post("/api/teams", json={"name": "Other", "graph": _graph()}).json()
        # edit the other template heavily
        big = TeamGraph(
            nodes=[
                GraphNode(spec=AgentSpec(id="lead", name="Lead", is_entry_point=True)),
                GraphNode(spec=AgentSpec(id="extra", name="Extra")),
            ]
        ).model_dump()
        client.put(f"/api/teams/{other['id']}/graph", json=big)

        # the running session still sees only its own pinned graph (lead only)
        session = app.state.sessions.get(app.state.default_session_id)
        assert session.registry.agent_ids() == ["lead"]


def test_editing_the_sessions_own_team_syncs_the_session(tmp_path):
    """Editing the team the session is bound to DOES sync (the MVP convenience —
    the canvas is the live control room for that session)."""
    app = create_app(db_path=tmp_path / "t.sqlite", repo_path=tmp_path / "repo")
    with TestClient(app) as client:
        default_id = app.state.default_team_id
        big = TeamGraph(
            nodes=[
                GraphNode(spec=AgentSpec(id="lead", name="Lead", is_entry_point=True)),
                GraphNode(spec=AgentSpec(id="react", name="React")),
            ]
        ).model_dump()
        client.put(f"/api/teams/{default_id}/graph", json=big)
        session = app.state.sessions.get(app.state.default_session_id)
        assert set(session.registry.agent_ids()) == {"lead", "react"}


def test_create_team_rejects_malformed_graph(tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite", repo_path=tmp_path / "repo")
    with TestClient(app) as client:
        bad = TeamGraph(
            nodes=[GraphNode(spec=AgentSpec(id="a", name="A"))],
        ).model_dump()
        bad["edges"] = [{"id": "e1", "source": "a", "target": "ghost", "label": ""}]
        r = client.post("/api/teams", json={"name": "Bad", "graph": bad})
        assert r.status_code == 422
