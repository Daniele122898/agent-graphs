"""OpenCodeHarness end-to-end against the deterministic fake server (no LLM, no
subprocess): execution, SSE→bus translation, lifecycle, history, usage, stop,
and the task continuation nudge — the same behaviors the native harness has."""

from __future__ import annotations

import asyncio

import pytest

from backend.domain.models import AgentSpec, Capabilities, GraphEdge, GraphNode, TeamGraph
from backend.harness.opencode.harness import OpenCodeHarness
from backend.runtime.sessions import SessionManager
from backend.storage.agent_state import AgentStateStore
from backend.storage.teams import TeamStore
from tests._fake_opencode import FakeOpenCodeClient, fake_connect, text_part, tool_part


def _graph() -> TeamGraph:
    lead = AgentSpec(id="lead", name="Lead", is_entry_point=True, model="lmstudio:qwen/qwen3.5-9b",
                     capabilities=Capabilities.from_level("read-write"))
    expert = AgentSpec(id="expert", name="Expert", model="lmstudio:qwen/qwen3.5-9b",
                       capabilities=Capabilities.from_level("read"))
    return TeamGraph(nodes=[GraphNode(spec=lead), GraphNode(spec=expert)],
                     edges=[GraphEdge(id="e1", source="lead", target="expert", label="ask")])


def _opencode_session(conn, fake_clock, repo, client: FakeOpenCodeClient):
    graph = _graph()
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    session = SessionManager(conn, clock=fake_clock).create_session(
        team_id=team.id, repo_path=repo, graph=graph
    )
    # swap in the opencode harness wired to the fake server
    session.harness = OpenCodeHarness(
        state_store=AgentStateStore(conn, clock=fake_clock),
        message_log=session.harness.message_log,
        connect=fake_connect(client),
    )
    return session


async def _collect_bus(session, stop_evt):
    events = []
    async def run():
        async for e in session.bus.subscribe():
            events.append(e)
            if stop_evt.is_set():
                return
    return events, asyncio.create_task(run())


async def test_run_to_completion_streams_tools_and_returns_output(conn, fake_clock, repo):
    client = FakeOpenCodeClient({
        "lead": [
            [tool_part("write", "c1", {"filePath": "hello.txt", "content": "banana"}, "Wrote file.")],
            [text_part("done — created hello.txt")],
        ]
    })
    session = _opencode_session(conn, fake_clock, repo, client)
    events: list = []
    sub = asyncio.create_task(_drain(session, events))
    await asyncio.sleep(0.02)

    out = await session.harness.run_to_completion(session, "lead", "make hello.txt")
    await asyncio.sleep(0.05)

    types = [e["type"] for e in events]
    assert "user_message" in types
    assert any(e["type"] == "tool_call" and e["data"]["tool"] == "write" for e in events)
    assert any(e["type"] == "tool_result" for e in events)
    assert "agent_done" in types
    assert session.registry.lifecycle("lead") in ("idle", "running")

    # second prompt continues on the same session and returns its text
    out2 = await session.harness.run_to_completion(session, "lead", "now report")
    assert "hello.txt" in out2

    # usage accumulated from message tokens
    u = session.harness.usage(session, "lead")
    assert u["requests"] >= 1 and u["input_tokens"] > 0
    await session.harness.shutdown(session)
    sub.cancel()


async def _drain(session, sink):
    try:
        async for e in session.bus.subscribe():
            sink.append(e)
    except asyncio.CancelledError:
        pass


async def test_submit_runs_in_background_and_announces_done(conn, fake_clock, repo):
    client = FakeOpenCodeClient({"lead": [[text_part("hi there")]]})
    session = _opencode_session(conn, fake_clock, repo, client)
    events: list = []
    sub = asyncio.create_task(_drain(session, events))
    await asyncio.sleep(0.02)

    await session.harness.submit(session, "lead", "say hi")
    # submit returns immediately; the run completes in the background
    for _ in range(100):
        if any(e["type"] == "agent_done" for e in events):
            break
        await asyncio.sleep(0.02)
    assert any(e["type"] == "agent_done" for e in events)
    await session.harness.shutdown(session)
    sub.cancel()


async def test_history_renders_transcript(conn, fake_clock, repo):
    client = FakeOpenCodeClient({"lead": [[tool_part("read", "c1", {"path": "x"}, "contents"), text_part("read it")]]})
    session = _opencode_session(conn, fake_clock, repo, client)

    # history before any run: empty rows but real instructions
    view0 = await session.harness.history(session, "lead")
    assert view0.message_count == 0 and view0.rows == []
    assert view0.instructions and "Lead" in view0.instructions[0]

    await session.harness.run_to_completion(session, "lead", "read x")
    view = await session.harness.history(session, "lead")
    kinds = [r["kind"] for r in view.rows]
    assert "user" in kinds
    assert "tool_call" in kinds and "tool_result" in kinds
    assert "text" in kinds
    await session.harness.shutdown(session)


async def test_run_for_task_nudges_until_todos_done(conn, fake_clock, repo):
    # turn 1 leaves an open todo; turn 2 clears it. run_for_task should re-prompt.
    client = FakeOpenCodeClient({
        "lead": [
            {"parts": [text_part("starting")], "todos": [{"content": "build", "status": "in_progress"}]},
            {"parts": [text_part("finished")], "todos": [{"content": "build", "status": "completed"}]},
        ]
    })
    session = _opencode_session(conn, fake_clock, repo, client)
    out = await session.harness.run_for_task(session, "lead", "do the work")
    assert "finished" in out
    # two prompts were issued to the lead's session (initial + one nudge)
    assert client._turn["ses_fake1"] == 2
    await session.harness.shutdown(session)


async def test_stop_aborts_and_frees(conn, fake_clock, repo):
    client = FakeOpenCodeClient({"lead": [[text_part("ok")]]})
    session = _opencode_session(conn, fake_clock, repo, client)
    await session.harness.run_to_completion(session, "lead", "go")
    await session.harness.stop(session, "lead")  # idempotent, no error
    assert session.registry.lifecycle("lead") == "idle"
    await session.harness.shutdown(session)


async def test_reviewer_parses_json_verdict(conn, fake_clock, repo):
    client = FakeOpenCodeClient({
        "expert": [[text_part('{"approved": true, "critique": "looks good"}')]],
    })
    session = _opencode_session(conn, fake_clock, repo, client)
    verdict = await session.harness.run_reviewer(session, "expert", "the task", "the result")
    assert verdict.approved is True and "good" in verdict.critique
    await session.harness.shutdown(session)


async def test_ask_user_parks_and_resumes_on_answer(conn, fake_clock, repo):
    client = FakeOpenCodeClient({
        "lead": [{"question": {"question": "Which color?", "header": "color",
                               "options": [{"label": "red"}, {"label": "blue"}]}}],
    })
    session = _opencode_session(conn, fake_clock, repo, client)
    events: list = []
    sub = asyncio.create_task(_drain(session, events))
    await asyncio.sleep(0.02)

    # the run parks on the question — start it without awaiting
    run = asyncio.create_task(session.harness.run_to_completion(session, "lead", "pick a color"))

    # the question surfaces (cached, listable) + waiting-on-user lifecycle
    q = None
    for _ in range(100):
        qs = session.harness.list_questions(session)
        if qs:
            q = qs[0]; break
        await asyncio.sleep(0.02)
    assert q is not None, "question never surfaced"
    assert q["agent_id"] == "lead"
    assert q["questions"][0]["question"] == "Which color?"
    assert q["questions"][0]["options"] == ["red", "blue"]
    assert any(e["type"] == "user_question" for e in events)
    assert session.registry.lifecycle("lead") == "waiting-on-user"

    # answer it → the parked run resumes and completes
    ok = await session.harness.answer_question(session, q["id"], ["blue"])
    assert ok
    out = await asyncio.wait_for(run, timeout=2)
    assert "blue" in out
    assert any(e["type"] == "user_question_done" for e in events)
    assert session.harness.list_questions(session) == []  # cleared
    await session.harness.shutdown(session)
    sub.cancel()


async def test_delegate_runs_target_and_logs(conn, fake_clock, repo):
    # lead -> expert delegation through the harness's base delegate() path
    client = FakeOpenCodeClient({"expert": [[text_part("call it result.txt")]]})
    session = _opencode_session(conn, fake_clock, repo, client)
    events: list = []
    sub = asyncio.create_task(_drain(session, events))
    await asyncio.sleep(0.02)

    answer = await session.harness.delegate(session, "lead", "expert", "what file name?")
    assert "result.txt" in answer
    await asyncio.sleep(0.05)  # let the bus drain task process the published events
    assert any(e["type"] == "a2a_message" and e["data"]["kind"] == "question" for e in events)
    assert any(e["type"] == "a2a_message" and e["data"]["kind"] == "reply" for e in events)
    logged = session.harness.message_log.for_session(session.id)
    assert any(m["from_agent"] == "lead" and m["to_agent"] == "expert" for m in logged)
    await session.harness.shutdown(session)
    sub.cancel()


async def test_delegate_enforces_neighbor_guard(conn, fake_clock, repo):
    import pytest
    from pydantic_ai import ModelRetry
    client = FakeOpenCodeClient({"lead": [[text_part("x")]]})
    session = _opencode_session(conn, fake_clock, repo, client)
    with pytest.raises(ModelRetry):  # edge is lead->expert only
        await session.harness.delegate(session, "expert", "lead", "reverse edge not allowed")
    await session.harness.shutdown(session)
