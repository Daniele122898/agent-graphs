"""Multi-session: launching sessions is isolated, same-repo launches warn, and
session-scoped endpoints target the right session."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app
from tests.conftest import bootstrap_session


def test_launch_lists_and_isolates_sessions(tmp_path):
    app = create_app(db_path=tmp_path / "m.sqlite")
    with TestClient(app) as client:
        assert client.get("/api/sessions").json()["sessions"] == []  # empty start

        team, a = bootstrap_session(client, tmp_path / "repoA")
        launched = client.post(
            "/api/sessions",
            json={"team_id": team["id"], "repo_path": str(tmp_path / "repoB"), "mode": "serial"},
        ).json()
        assert launched["repo_path"] == str((tmp_path / "repoB").resolve())
        assert launched["mode"] == "serial"
        assert launched["warning"] is None
        assert launched["id"] != a["id"]

        assert len(client.get("/api/sessions").json()["sessions"]) == 2

        live_a = app.state.sessions.get(a["id"])
        live_b = app.state.sessions.get(launched["id"])
        assert live_a.bus is not live_b.bus
        assert live_a.write_lock is not live_b.write_lock
        assert live_a.gateway is not live_b.gateway
        assert live_b.gateway.mode == "serial" and live_a.gateway.mode == "parallel"


def test_same_repo_launch_warns_but_allows(tmp_path):
    app = create_app(db_path=tmp_path / "m.sqlite")
    with TestClient(app) as client:
        team, _a = bootstrap_session(client, tmp_path / "repoA")
        launched = client.post(
            "/api/sessions",
            json={"team_id": team["id"], "repo_path": str(tmp_path / "repoA"), "mode": "parallel"},
        ).json()
        assert launched["warning"] is not None
        assert "already bound" in launched["warning"]


def test_session_scoped_endpoints_target_the_right_session(tmp_path):
    app = create_app(db_path=tmp_path / "m.sqlite")
    with TestClient(app) as client:
        team, a = bootstrap_session(client, tmp_path / "repoA")
        b = client.post(
            "/api/sessions",
            json={"team_id": team["id"], "repo_path": str(tmp_path / "repoB"), "mode": "parallel"},
        ).json()
        client.post(
            f"/api/tasks?session_id={b['id']}",
            json={"prompt": "noop", "assigned_agent_id": "lead", "completion_signal": "self_reported"},
        )
        assert len(client.get(f"/api/tasks?session_id={b['id']}").json()["tasks"]) == 1
        assert len(client.get(f"/api/tasks?session_id={a['id']}").json()["tasks"]) == 0
