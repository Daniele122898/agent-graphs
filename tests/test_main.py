"""App-level smoke for the explicit team/session flow (no auto-create)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app
from tests.conftest import bootstrap_session


def test_health_starts_empty_then_reflects_a_launched_session(tmp_path):
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert {"teams", "sessions", "agent_state", "tasks", "messages"} <= set(body["tables"])
        assert body["sessions"] == 0  # nothing auto-created

        bootstrap_session(client, tmp_path / "repo")
        assert client.get("/health").json()["sessions"] == 1


def test_launched_session_bound_to_repo(tmp_path):
    repo = tmp_path / "repo"
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        _team, session = bootstrap_session(client, repo)
        info = client.get(f"/api/session?session_id={session['id']}").json()
        assert info["repo_path"] == str(repo.resolve())
        assert info["status"] == "active"


def test_new_team_has_starter_entry_point(tmp_path):
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        team = client.post("/api/teams", json={"name": "My Team"}).json()
        full = client.get(f"/api/teams/{team['id']}").json()
        entry = [n["spec"]["id"] for n in full["graph"]["nodes"] if n["spec"]["is_entry_point"]]
        assert entry == ["lead"]


def test_session_required_for_scoped_endpoints(tmp_path):
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        # no session_id → 400 (no implicit default)
        assert client.get("/api/session").status_code == 422  # missing required query param
