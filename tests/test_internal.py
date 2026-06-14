"""The /internal/ask_agent callback the OpenCode ask_agent tool POSTs to:
token auth + routing through Harness.delegate (guards + target run). Uses the
fake OpenCode server (no real subprocess/LLM)."""

from __future__ import annotations

from fastapi.testclient import TestClient

import backend.harness.opencode.harness as oc_harness
from backend.main import create_app
from tests._fake_opencode import FakeConnection, FakeOpenCodeClient, text_part

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

        # good token, valid neighbor -> 200 + the target's answer
        ok = client.post("/internal/ask_agent", headers={"x-ag-token": token}, json={
            "session_id": sid, "asker_id": "lead", "target_id": "expert", "question": "what file name?"})
        assert ok.status_code == 200
        assert "result.txt" in ok.text

        # guard violation (non-neighbor) -> 409 with the corrective message
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
        assert "result.txt" in by_name.text
