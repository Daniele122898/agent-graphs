"""App-level smoke: the auto-created team + session are wired and reachable."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app


def test_health_reports_four_tables(tmp_path):
    app = create_app(db_path=tmp_path / "app.sqlite", repo_path=tmp_path / "repo")
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert {"teams", "sessions", "agent_state", "tasks"} <= set(body["tables"])
        assert body["sessions"] == 1


def test_default_session_bound_to_repo(tmp_path):
    repo = tmp_path / "repo"
    app = create_app(db_path=tmp_path / "app.sqlite", repo_path=repo)
    with TestClient(app) as client:
        r = client.get("/api/session")
        assert r.status_code == 200
        info = r.json()
        assert info["repo_path"] == str(repo.resolve())
        assert info["status"] == "active"


def test_default_team_has_entry_point(tmp_path):
    app = create_app(db_path=tmp_path / "app.sqlite", repo_path=tmp_path / "repo")
    with TestClient(app) as client:
        team = client.get("/api/team").json()
        entry_points = [n["spec"]["id"] for n in team["graph"]["nodes"] if n["spec"]["is_entry_point"]]
        assert entry_points == ["lead"]
