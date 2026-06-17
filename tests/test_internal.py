"""The /internal/ask_agent + /internal/ask_team callbacks the OpenCode tools POST
to: token auth + NON-BLOCKING dispatch (validate synchronously, run the target in
the background, inject the reply into the asker). Uses the fake OpenCode server
(no real subprocess/LLM)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

import backend.harness.opencode.harness as oc_harness
from backend.main import create_app
from tests._fake_opencode import FakeConnection, FakeOpenCodeClient, text_part


def _wait_for(predicate, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()

GRAPH = {
    "nodes": [
        {"spec": {"id": "lead", "name": "Lead", "is_entry_point": True, "model": "lmstudio:m",
                  "capabilities": {"filesystem": "read-write", "read_paths": ["**"], "write_paths": ["**"], "bash": True}}},
        {"spec": {"id": "expert", "name": "Expert", "model": "lmstudio:m",
                  "capabilities": {"filesystem": "read", "read_paths": ["**"], "write_paths": [], "bash": False}}},
    ],
    "edges": [{"id": "e1", "source": "lead", "target": "expert", "label": "ask"}],
}


def _launch_opencode_session(client, tmp_path, fake_client):
    team = client.post("/api/teams", json={"name": "T", "graph": GRAPH}).json()
    return client.post("/api/sessions", json={
        "team_id": team["id"], "repo_path": str(tmp_path / "repo"), "harness": "opencode",
    }).json()


GRAPH_FANOUT = {
    "nodes": [
        {"spec": {"id": "lead", "name": "Lead", "is_entry_point": True, "model": "lmstudio:m",
                  "capabilities": {"filesystem": "read-write", "read_paths": ["**"], "write_paths": ["**"], "bash": True}}},
        {"spec": {"id": "fe", "name": "Frontend", "model": "lmstudio:m",
                  "capabilities": {"filesystem": "read", "read_paths": ["**"], "write_paths": [], "bash": False}}},
        {"spec": {"id": "be", "name": "Backend", "model": "lmstudio:m",
                  "capabilities": {"filesystem": "read", "read_paths": ["**"], "write_paths": [], "bash": False}}},
    ],
    "edges": [{"id": "e1", "source": "lead", "target": "fe", "label": "ui"},
              {"id": "e2", "source": "lead", "target": "be", "label": "api"}],
}


def test_ask_team_callback_fans_out(tmp_path, monkeypatch):
    fake = FakeOpenCodeClient({"fe": [[text_part("frontend answer")]], "be": [[text_part("backend answer")]]})
    monkeypatch.setattr(oc_harness, "_default_connect", lambda session, token: FakeConnection(fake))

    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        team = client.post("/api/teams", json={"name": "TF", "graph": GRAPH_FANOUT}).json()
        session = client.post("/api/sessions", json={
            "team_id": team["id"], "repo_path": str(tmp_path / "repo"), "harness": "opencode"}).json()
        sid = session["id"]
        live = app.state.sessions.get(sid)
        token = live.harness.token_for(live)

        # fan out to both teammates at once (one by display name, one by id).
        # Non-blocking: 200 + an immediate ACK; the answers are delivered via the
        # message log (target replies) when the background runs finish.
        ok = client.post("/internal/ask_team", headers={"x-ag-token": token}, json={
            "session_id": sid, "asker_id": "lead",
            "assignments": [{"target_id": "Frontend", "task": "ui"}, {"target_id": "be", "task": "api"}]})
        assert ok.status_code == 200
        assert "Delegated" in ok.text
        replied = lambda who: any(  # noqa: E731
            m["from_agent"] == who and m["to_agent"] == "lead" and m["kind"] == "reply"
            for m in app.state.messages.for_session(sid))
        assert _wait_for(lambda: replied("fe") and replied("be")), "both teammates should reply (fan-out)"
        bodies = " ".join(m["body"] for m in app.state.messages.for_session(sid))
        assert "frontend answer" in bodies and "backend answer" in bodies

        # bad token -> 403
        bad = client.post("/internal/ask_team", headers={"x-ag-token": "nope"}, json={
            "session_id": sid, "asker_id": "lead", "assignments": [{"target_id": "fe", "task": "x"}]})
        assert bad.status_code == 403


def test_ask_agent_callback_routes_through_delegate(tmp_path, monkeypatch):
    fake = FakeOpenCodeClient({"expert": [[text_part("call it result.txt")]]})
    monkeypatch.setattr(oc_harness, "_default_connect", lambda session, token: FakeConnection(fake))

    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        session = _launch_opencode_session(client, tmp_path, fake)
        assert session["harness"] == "opencode"
        sid = session["id"]
        live = app.state.sessions.get(sid)
        token = live.harness.token_for(live)

        # bad token -> 403
        bad = client.post("/internal/ask_agent", headers={"x-ag-token": "nope"}, json={
            "session_id": sid, "asker_id": "lead", "target_id": "expert", "question": "name?"})
        assert bad.status_code == 403

        # good token, valid neighbor -> 200 + an immediate ACK (non-blocking).
        # The target's answer is delivered via the message log when the background
        # run finishes, NOT in the HTTP response.
        ok = client.post("/internal/ask_agent", headers={"x-ag-token": token}, json={
            "session_id": sid, "asker_id": "lead", "target_id": "expert", "question": "what file name?"})
        assert ok.status_code == 200
        assert "Delegated" in ok.text and "result.txt" not in ok.text
        assert _wait_for(lambda: any(
            m["from_agent"] == "expert" and m["to_agent"] == "lead" and m["kind"] == "reply"
            and "result.txt" in m["body"] for m in app.state.messages.for_session(sid)))
        assert any(m["from_agent"] == "lead" and m["to_agent"] == "expert" and m["kind"] == "question"
                   for m in app.state.messages.for_session(sid))

        # guard violation (non-neighbor) -> 409 with the corrective message
        # (validated SYNCHRONOUSLY, before any background run)
        bad_edge = client.post("/internal/ask_agent", headers={"x-ag-token": token}, json={
            "session_id": sid, "asker_id": "expert", "target_id": "lead", "question": "reverse"})
        assert bad_edge.status_code == 409
        assert "consult" in bad_edge.text.lower()

        # unknown session -> 404
        missing = client.post("/internal/ask_agent", headers={"x-ag-token": token}, json={
            "session_id": "sess_nope", "asker_id": "lead", "target_id": "expert", "question": "q"})
        assert missing.status_code == 404

        # target given by DISPLAY NAME (mixed case + whitespace) resolves to the
        # canonical id — the reported "'Planner' is not someone you can consult" bug
        by_name = client.post("/internal/ask_agent", headers={"x-ag-token": token}, json={
            "session_id": sid, "asker_id": "lead", "target_id": "  Expert  ", "question": "what file name?"})
        assert by_name.status_code == 200
        assert "Delegated" in by_name.text
