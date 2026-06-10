"""ask_user: the run parks on the human's answer and resumes with it; the
continuation nudge keeps a task working while its todos are open."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient
from pydantic_ai.messages import TextPart, ToolCallPart

import backend.wiring as wiring
from backend.main import create_app
from tests.conftest import bootstrap_session, make_sequence_model


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_ask_user_parks_the_run_and_resumes_with_the_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wiring,
        "resolve_model",
        lambda s: make_sequence_model([
            [ToolCallPart("ask_user", {"questions": [
                {"question": "Which word length?", "options": ["5", "6"]},
            ]})],
            [TextPart("building with the chosen length")],
        ]),
    )
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        _team, session = bootstrap_session(client, tmp_path / "repo")
        sid = session["id"]
        client.post(f"/api/agent/lead/run?session_id={sid}", json={"prompt": "make wordle"})

        # the question shows up as pending, with the agent visibly waiting
        qs = _wait(lambda: client.get(f"/api/questions?session_id={sid}").json()["questions"])
        assert qs[0]["agent_id"] == "lead"
        assert qs[0]["questions"][0]["question"] == "Which word length?"
        assert qs[0]["questions"][0]["options"] == ["5", "6"]

        # wrong answer count is a clean 422; unknown id a 404
        bad = client.post(f"/api/questions/{qs[0]['id']}/answer?session_id={sid}", json={"answers": ["5", "x"]})
        assert bad.status_code == 422
        assert client.post(f"/api/questions/nope/answer?session_id={sid}", json={"answers": ["5"]}).status_code == 404

        ok = client.post(f"/api/questions/{qs[0]['id']}/answer?session_id={sid}", json={"answers": ["5"]})
        assert ok.status_code == 200

        # the run resumes: the answer reaches the model as the tool result and
        # the next turn completes the run
        def finished():
            rows = client.get(f"/api/agent/lead/history?session_id={sid}").json()["rows"]
            return rows if any(r["kind"] == "text" for r in rows) else None

        rows = _wait(finished)
        tool_results = [r for r in rows if r["kind"] == "tool_result" and r["tool"] == "ask_user"]
        assert tool_results and "A: 5" in tool_results[0]["text"]
        # nothing left pending
        assert client.get(f"/api/questions?session_id={sid}").json()["questions"] == []


def test_task_with_open_todos_gets_nudged_to_continue(tmp_path, monkeypatch):
    """A task run that ends mid-checklist is re-prompted (capped) instead of
    being silently marked done — the anti-stall mechanism."""
    monkeypatch.setattr(
        wiring,
        "resolve_model",
        lambda s: make_sequence_model([
            # run 1: lays out a plan, then stops without finishing
            [ToolCallPart("write_todos", {"todos": [
                {"content": "write the game", "status": "in_progress"},
                {"content": "test it", "status": "pending"},
            ]})],
            [TextPart("I have planned the work.")],
            # nudge run: completes the checklist and finishes properly
            [ToolCallPart("write_todos", {"todos": [
                {"content": "write the game", "status": "completed"},
                {"content": "test it", "status": "completed"},
            ]})],
            [TextPart("all done")],
        ]),
    )
    app = create_app(db_path=tmp_path / "app.sqlite")
    with TestClient(app) as client:
        _team, session = bootstrap_session(client, tmp_path / "repo")
        sid = session["id"]
        task = client.post(f"/api/tasks?session_id={sid}", json={"prompt": "build the game"}).json()

        got = _wait(lambda: (lambda t: t if t["status"] == "done" else None)(
            client.get(f"/api/tasks/{task['id']}").json()
        ))
        assert got["result"] == "all done", "the nudged continuation's output should win"
