"""API-level end-to-end on an OpenCode session through create_app (mirrors the
native spine): launch an opencode session, run the task system, and observe the
work via the same HTTP surface — all on the deterministic fake server."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

import backend.harness.opencode.harness as oc_harness
from backend.main import create_app
from tests._fake_opencode import FakeConnection, FakeOpenCodeClient, text_part, tool_part

GRAPH = {
    "nodes": [{"spec": {"id": "lead", "name": "Lead", "is_entry_point": True, "model": "lmstudio:m",
               "capabilities": {"filesystem": "read-write", "read_paths": ["**"], "write_paths": ["**"], "bash": True}}}],
    "edges": [],
}


def _wait(fn, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(0.03)
    return fn()


def test_task_runs_to_done_on_opencode_session(tmp_path, monkeypatch):
    fake = FakeOpenCodeClient({"lead": [
        [tool_part("write", "c1", {"filePath": "out.txt", "content": "hi"}, "Wrote file."), text_part("done — wrote out.txt")],
    ]})
    monkeypatch.setattr(oc_harness, "_default_connect", lambda session, token: FakeConnection(fake))

    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        team = client.post("/api/teams", json={"name": "T", "graph": GRAPH}).json()
        session = client.post("/api/sessions", json={
            "team_id": team["id"], "repo_path": str(tmp_path / "repo"), "harness": "opencode"}).json()
        sid = session["id"]
        assert session["harness"] == "opencode"

        task = client.post(f"/api/tasks?session_id={sid}", json={"prompt": "write out.txt", "assigned_agent_id": "lead"}).json()
        done = _wait(lambda: (lambda t: t if t["status"] in ("done", "blocked", "failed") else None)(
            client.get(f"/api/tasks/{task['id']}").json()))
        assert done["status"] == "done", done

        # the transcript is observable via the same history endpoint as native
        hist = client.get(f"/api/agent/lead/history?session_id={sid}").json()
        kinds = [r["kind"] for r in hist["rows"]]
        assert "tool_call" in kinds and "text" in kinds
        assert hist["instructions"] and "Lead" in hist["instructions"][0]

        # usage is reported through the same stats endpoint
        usage = client.get(f"/api/stats/usage/lead?session_id={sid}").json()
        assert usage["requests"] >= 1
