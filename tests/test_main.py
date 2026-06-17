"""App-level smoke for the explicit team/session flow (no auto-create)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient
from pydantic_ai.messages import TextPart

import backend.wiring as wiring
from backend.main import create_app
from tests.conftest import bootstrap_session, make_sequence_model


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


def test_orphaned_inflight_tasks_blocked_on_restart(tmp_path):
    """A task mid-flight when the process dies has no runner in the next
    process — restart must park it in blocked (with a note), not leave it
    'running' forever."""
    db = tmp_path / "app.sqlite"
    app = create_app(db_path=db)
    with TestClient(app) as client:
        _team, session = bootstrap_session(client, tmp_path / "repo")
        # a task left mid-flight (created via the store so no runner — and no
        # model call — is involved; the row is what survives the crash)
        task = app.state.tasks.create(
            session_id=session["id"], title="T", prompt="p", assigned_agent_id="lead"
        )
        app.state.tasks.set_status(task.id, "running")

    app2 = create_app(db_path=db)
    with TestClient(app2) as client:
        got = client.get(f"/api/tasks/{task.id}").json()
        assert got["status"] == "blocked"
        assert "orphaned" in got["result"]


def test_retry_reruns_a_blocked_task_in_place(tmp_path, monkeypatch):
    """The Retry button's contract: a blocked task re-runs as the same row (no
    copy/re-create), the stale error result is cleared, and it can reach done."""
    monkeypatch.setattr(wiring, "resolve_model", lambda s: make_sequence_model([[TextPart("fixed it")]]))
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        _team, session = bootstrap_session(client, tmp_path / "repo")
        task = app.state.tasks.create(
            session_id=session["id"], title="T", prompt="p", assigned_agent_id="lead"
        )
        app.state.tasks.set_result(task.id, "error: model exploded")
        app.state.tasks.set_status(task.id, "blocked")

        assert client.post(f"/api/tasks/{task.id}/retry").status_code == 200

        deadline = time.time() + 5
        got = {}
        while time.time() < deadline:
            got = client.get(f"/api/tasks/{task.id}").json()
            if got["status"] == "done":
                break
            time.sleep(0.05)
        assert got["status"] == "done"
        assert got["result"] == "fixed it"

        # only blocked tasks are retryable
        assert client.post(f"/api/tasks/{task.id}/retry").status_code == 409
        assert client.post("/api/tasks/nope/retry").status_code == 404


def test_single_process_serves_built_frontend_without_shadowing_api(tmp_path):
    """Single-process mode: when frontend/dist exists, the backend serves the SPA
    at '/' (so an agent editing this repo can't HMR/reload-break a live session),
    but the API/SSE routes still win — the static mount is last."""
    from pathlib import Path

    dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        # API/health always work regardless
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/teams").status_code == 200
        root = client.get("/")
        if dist.is_dir():  # only when the UI has been built
            assert root.status_code == 200 and "text/html" in root.headers.get("content-type", "")
        else:  # no dist → no SPA mount; '/' is simply not a route
            assert root.status_code in (404, 405)


def test_session_required_for_scoped_endpoints(tmp_path):
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        # no session_id → 400 (no implicit default)
        assert client.get("/api/session").status_code == 422  # missing required query param


def _seed_history(app, session_id: str, agent_id: str = "lead"):
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart as TP, ToolCallPart, ToolReturnPart, UserPromptPart

    msgs = [
        ModelRequest(parts=[UserPromptPart(content="make a game")]),
        ModelResponse(parts=[ToolCallPart("write_file", {"path": "g.py", "content": "x"})]),
        ModelRequest(parts=[ToolReturnPart(tool_name="write_file", content="wrote g.py", tool_call_id="1")]),
        ModelResponse(parts=[TP(content="made it")]),
    ]
    app.state.agent_state.save(session_id, agent_id, messages=msgs)
    return msgs


def test_agent_history_shows_the_real_model_context(tmp_path):
    """The history endpoint returns what the model actually sees: the system
    sections (persona/capabilities, environment) and the stored conversation
    rendered as transcript rows."""
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        _team, session = bootstrap_session(client, tmp_path / "repo")
        _seed_history(app, session["id"])

        body = client.get(f"/api/agent/lead/history?session_id={session['id']}").json()
        joined = "\n".join(body["instructions"])
        assert "lead engineer" in joined  # persona
        assert "Today's date" in joined  # environment, sent last
        assert [r["kind"] for r in body["rows"]] == ["user", "tool_call", "tool_result", "text"]
        assert body["rows"][0]["text"] == "make a game"
        assert body["rows"][1]["tool"] == "write_file"
        assert body["message_count"] == 4


def test_clear_history_resets_the_conversation(tmp_path):
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        _team, session = bootstrap_session(client, tmp_path / "repo")
        _seed_history(app, session["id"])
        sid = session["id"]

        assert client.post(f"/api/agent/lead/history/clear?session_id={sid}").status_code == 200
        body = client.get(f"/api/agent/lead/history?session_id={sid}").json()
        assert body["rows"] == []
        assert body["instructions"]  # identity survives — instructions are rebuilt per request


def test_summarize_history_compacts_to_summary_plus_ack(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wiring, "resolve_model", lambda s: make_sequence_model([[TextPart("SUMMARY: built g.py")]])
    )
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        _team, session = bootstrap_session(client, tmp_path / "repo")
        _seed_history(app, session["id"])
        sid = session["id"]

        r = client.post(f"/api/agent/lead/history/summarize?session_id={sid}")
        assert r.status_code == 200
        rows = client.get(f"/api/agent/lead/history?session_id={sid}").json()["rows"]
        assert len(rows) == 2
        assert rows[0]["kind"] == "user" and "SUMMARY: built g.py" in rows[0]["text"]

        # nothing left to summarize twice... except the summary itself — but an
        # empty history is a clean 409
        client.post(f"/api/agent/lead/history/clear?session_id={sid}")
        assert client.post(f"/api/agent/lead/history/summarize?session_id={sid}").status_code == 409
