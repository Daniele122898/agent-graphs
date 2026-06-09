"""Multi-session: launching a second session is isolated from the first, and
launching onto an already-bound repo warns (but is allowed)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app


def test_launch_lists_and_isolates_sessions(tmp_path):
    app = create_app(db_path=tmp_path / "m.sqlite", repo_path=tmp_path / "repoA")
    with TestClient(app) as client:
        default = client.get("/api/session").json()
        team_id = default["team_id"]

        # one session at startup
        assert len(client.get("/api/sessions").json()["sessions"]) == 1

        launched = client.post(
            "/api/sessions",
            json={"team_id": team_id, "repo_path": str(tmp_path / "repoB"), "mode": "serial"},
        ).json()
        assert launched["repo_path"] == str((tmp_path / "repoB").resolve())
        assert launched["mode"] == "serial"
        assert launched["warning"] is None
        assert launched["id"] != default["id"]

        sessions = client.get("/api/sessions").json()["sessions"]
        assert len(sessions) == 2

        # the two live sessions own independent infrastructure
        a = app.state.sessions.get(default["id"])
        b = app.state.sessions.get(launched["id"])
        assert a.bus is not b.bus
        assert a.write_lock is not b.write_lock
        assert a.gateway is not b.gateway
        assert b.gateway.mode == "serial" and a.gateway.mode == "parallel"


def test_same_repo_launch_warns_but_allows(tmp_path):
    app = create_app(db_path=tmp_path / "m.sqlite", repo_path=tmp_path / "repoA")
    with TestClient(app) as client:
        team_id = client.get("/api/session").json()["team_id"]
        # launch onto the SAME repo the default session uses
        launched = client.post(
            "/api/sessions",
            json={"team_id": team_id, "repo_path": str(tmp_path / "repoA"), "mode": "parallel"},
        ).json()
        assert launched["warning"] is not None
        assert "already bound" in launched["warning"]


def test_session_scoped_endpoints_target_the_right_session(tmp_path):
    """A task created with ?session_id=<b> lands in session B, not the default."""
    app = create_app(db_path=tmp_path / "m.sqlite", repo_path=tmp_path / "repoA")
    with TestClient(app) as client:
        team_id = client.get("/api/session").json()["team_id"]
        b = client.post(
            "/api/sessions",
            json={"team_id": team_id, "repo_path": str(tmp_path / "repoB"), "mode": "parallel"},
        ).json()
        # self_reported task on session B's lead
        client.post(
            f"/api/tasks?session_id={b['id']}",
            json={"prompt": "noop", "assigned_agent_id": "lead", "completion_signal": "self_reported"},
        )
        assert len(client.get(f"/api/tasks?session_id={b['id']}").json()["tasks"]) == 1
        # the default session has no tasks
        assert len(client.get("/api/tasks").json()["tasks"]) == 0
