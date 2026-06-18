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

        created = client.post(
            "/api/teams",
            json={"name": "Web Squad", "description": "ships the marketing site", "graph": _graph()},
        ).json()
        assert created["name"] == "Web Squad"
        assert created["description"] == "ships the marketing site"  # round-trips
        assert created["agent_count"] == 1  # summary shape: node count, not the full graph

        teams = client.get("/api/teams").json()["teams"]
        assert {t["name"] for t in teams} == {"Web Squad"}
        # the list is a lightweight summary (id/name/description/agent_count), NOT
        # the full team — it must not ship every team's graph topology.
        assert teams[0]["agent_count"] == 1
        assert "graph" not in teams[0]

        # PATCH is partial: renaming leaves the description untouched...
        renamed = client.patch(f"/api/teams/{created['id']}", json={"name": "Renamed"}).json()
        assert renamed["name"] == "Renamed"
        assert renamed["description"] == "ships the marketing site"
        # ...and editing the description leaves the name untouched.
        described = client.patch(
            f"/api/teams/{created['id']}", json={"description": "now the docs team"}
        ).json()
        assert described["name"] == "Renamed"
        assert described["description"] == "now the docs team"

        assert client.patch("/api/teams/nonexistent", json={"name": "x"}).status_code == 404


def test_delete_team_when_unbound(tmp_path):
    """A team no session is bound to deletes cleanly and disappears from the list."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team = client.post("/api/teams", json={"name": "Throwaway", "graph": _graph()}).json()
        assert client.delete(f"/api/teams/{team['id']}").status_code == 200
        assert client.get("/api/teams").json()["teams"] == []
        assert client.delete(f"/api/teams/{team['id']}").status_code == 404  # already gone


def test_delete_team_blocked_while_a_session_is_bound(tmp_path):
    """Deleting a team a session is bound to would orphan that session (resume
    404s after restart) — so it 409s, names the session, and leaves the team."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team, session = bootstrap_session(client, tmp_path / "repo", graph=_graph())
        r = client.delete(f"/api/teams/{team['id']}")
        assert r.status_code == 409
        assert "in use" in r.text.lower()
        assert client.get(f"/api/teams/{team['id']}").status_code == 200  # NOT deleted

        # Rebinding the session off the team frees it for deletion.
        other = client.post("/api/teams", json={"name": "Other", "graph": _graph()}).json()
        assert client.post(
            f"/api/sessions/{session['id']}/rebind", json={"team_id": other["id"]}
        ).status_code == 200
        assert client.delete(f"/api/teams/{team['id']}").status_code == 200


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


def test_rebind_refused_while_session_busy(tmp_path):
    """A rebind mid-run would corrupt the running conversation and orphan removed
    agents' workers — so it 409s if ANY agent is busy, and leaves the team unchanged."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team_a, session = bootstrap_session(client, tmp_path / "repo", graph=_graph("LeadA"))
        team_b = client.post("/api/teams", json={"name": "Team B", "graph": _graph("LeadB")}).json()
        live = app.state.sessions.get(session["id"])
        live.harness.is_busy = lambda _s, _a: True  # pretend the agent is mid-run

        r = client.post(f"/api/sessions/{session['id']}/rebind", json={"team_id": team_b["id"]})
        assert r.status_code == 409
        assert "busy" in r.text.lower()
        assert live.team_id == team_a["id"]  # NOT rebound — the guard fired before any mutation


def test_rebind_resets_history_for_repurposed_slot_but_keeps_it_for_same_role(tmp_path):
    """Identity for history carry-forward is (id, name). Reusing an id for a
    DIFFERENT role (name differs) must reset that slot's history so one role's
    conversation can't bleed into another; the SAME role (same id+name, even with
    a tweaked persona) keeps its history."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team_a, session = bootstrap_session(client, tmp_path / "repo", graph=_graph("Implementer"))
        live = app.state.sessions.get(session["id"])
        cleared: list[str] = []

        async def spy_clear(_s, agent_id):  # spy on the history reset
            cleared.append(agent_id)
        live.harness.clear_history = spy_clear

        # same id "lead", DIFFERENT name → repurposed slot → history reset
        repurposed = client.post("/api/teams", json={"name": "B", "graph": _graph("Frontend Expert")}).json()
        assert client.post(f"/api/sessions/{session['id']}/rebind", json={"team_id": repurposed["id"]}).status_code == 200
        assert cleared == ["lead"], "a repurposed (renamed) slot must have its carried history cleared"

        # same id "lead", SAME name → same agent → history preserved (no clear)
        cleared.clear()
        same_role = client.post("/api/teams", json={"name": "C", "graph": _graph("Frontend Expert")}).json()
        assert client.post(f"/api/sessions/{session['id']}/rebind", json={"team_id": same_role["id"]}).status_code == 200
        assert cleared == [], "same id + same name is the same agent — history must be kept"


def test_rebind_drops_persisted_state_of_removed_agents(tmp_path):
    """A removed agent's persisted row (history + oc_session_id) is deleted on
    rebind, so re-adding that id later can't silently inherit stale state — a
    removed id has no live identity to carry."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        big = TeamGraph(nodes=[
            GraphNode(spec=AgentSpec(id="lead", name="Lead", is_entry_point=True)),
            GraphNode(spec=AgentSpec(id="extra", name="Extra")),
        ]).model_dump()
        team_a, session = bootstrap_session(client, tmp_path / "repo", graph=big)
        sid = session["id"]
        # seed a persisted row for the agent that will be removed
        app.state.agent_state.set_lifecycle(sid, "extra", "idle")
        app.state.agent_state.set_oc_session(sid, "extra", "ses_old")
        assert app.state.agent_state.get(sid, "extra") is not None

        # rebind to a team WITHOUT "extra" → it's removed → its row is dropped
        team_b = client.post("/api/teams", json={"name": "B", "graph": _graph("Lead")}).json()
        assert client.post(f"/api/sessions/{sid}/rebind", json={"team_id": team_b["id"]}).status_code == 200
        assert app.state.agent_state.get(sid, "extra") is None, "removed agent's persisted row must be dropped"


def test_rebind_persists_team_id_to_db(tmp_path):
    """The new team_id is persisted (so a resume after restart binds the new team),
    routed through SessionManager.rebind rather than an ad-hoc write in the endpoint."""
    app = create_app(db_path=tmp_path / "t.sqlite")
    with TestClient(app) as client:
        team_a, session = bootstrap_session(client, tmp_path / "repo", graph=_graph("LeadA"))
        team_b = client.post("/api/teams", json={"name": "Team B", "graph": _graph("LeadB")}).json()
        assert client.post(f"/api/sessions/{session['id']}/rebind", json={"team_id": team_b["id"]}).status_code == 200
        row = app.state.conn.execute(
            "SELECT team_id FROM sessions WHERE id = ?", (session["id"],)
        ).fetchone()
        assert row["team_id"] == team_b["id"]
