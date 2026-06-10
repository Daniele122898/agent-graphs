"""RunningAgent: lifecycle, interjection-with-history, clean stop, persistence.

Driven with FunctionModel — deterministic, no tokens. A real Session is used so
the per-session bus/registry/lock are exercised as in production.
"""

from __future__ import annotations

import asyncio

from pydantic_ai.messages import TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from backend.agent_state import AgentStateStore
from backend.models_domain import AgentSpec, Capabilities, GraphNode, TeamGraph
from backend.runtime import RunningAgent
from backend.sessions import SessionManager
from backend.teams import TeamStore
from tests.conftest import make_sequence_model


def _session(conn, fake_clock, repo):
    teams = TeamStore(conn, clock=fake_clock)
    spec = AgentSpec(id="a", name="A", capabilities=Capabilities.from_level("read-write"))
    team = teams.create("T", TeamGraph(nodes=[GraphNode(spec=spec)]))
    mgr = SessionManager(conn, clock=fake_clock)
    return mgr.create_session(team_id=team.id, repo_path=repo, graph=team.graph), spec


async def _settle(running: RunningAgent, predicate, timeout=2.0):
    """Wait until predicate() is true (the loop is event-driven)."""
    loop_deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < loop_deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


async def test_running_agent_runs_then_returns_to_idle(conn, fake_clock, repo):
    session, spec = _session(conn, fake_clock, repo)
    model = make_sequence_model(
        [[ToolCallPart("write_file", {"path": "a.txt", "content": "hi"})], [TextPart("done")]]
    )
    ra = RunningAgent(session=session, spec=spec, model=model)
    session.registry.attach_running("a", ra)
    ra.start()
    ra.submit("make a.txt")

    await _settle(ra, lambda: (repo / "a.txt").exists() and session.registry.lifecycle("a") == "idle")
    assert (repo / "a.txt").read_text() == "hi"
    await ra.stop()


async def test_interjection_continues_with_history(conn, fake_clock, repo):
    session, spec = _session(conn, fake_clock, repo)
    # Two prompts, two completed runs. The second run sees the first run's
    # history (so message_history is threaded through).
    model = make_sequence_model([[TextPart("ok")]])
    ra = RunningAgent(session=session, spec=spec, model=model)
    session.registry.attach_running("a", ra)
    ra.start()

    ra.submit("first task")
    await _settle(ra, lambda: len(ra.messages) >= 2)
    first_len = len(ra.messages)

    ra.submit("second task")  # interjection
    await _settle(ra, lambda: len(ra.messages) > first_len)
    # history accumulated across both prompts (request+response each)
    assert len(ra.messages) > first_len
    await ra.stop()


async def test_stop_is_clean_and_does_not_mark_blocked(conn, fake_clock, repo):
    session, spec = _session(conn, fake_clock, repo)

    async def slow(messages, info):
        await asyncio.sleep(5)  # long-running model call
        return None  # never reached

    ra = RunningAgent(session=session, spec=spec, model=FunctionModel(slow))
    session.registry.attach_running("a", ra)
    ra.start()
    ra.submit("go")
    await _settle(ra, lambda: session.registry.lifecycle("a") == "running")
    await ra.stop()  # cancels mid-run
    assert session.registry.lifecycle("a") == "idle"  # not 'blocked'


async def test_state_persisted_after_run(conn, fake_clock, repo):
    session, spec = _session(conn, fake_clock, repo)
    store = AgentStateStore(conn, clock=fake_clock)
    model = make_sequence_model([[TextPart("done")]])
    ra = RunningAgent(session=session, spec=spec, model=model, state_store=store)
    session.registry.attach_running("a", ra)
    ra.start()
    ra.submit("go")
    await _settle(ra, lambda: store.get(session.id, "a") is not None and session.registry.lifecycle("a") == "idle")

    saved = store.get(session.id, "a")
    assert saved is not None
    # the persisted history round-trips back into loadable messages
    msgs = store.load_messages(session.id, "a")
    assert len(msgs) >= 2
    await ra.stop()


async def test_stop_cancels_an_inflight_run_once(conn, fake_clock, repo):
    """Stop must cancel a task/delegation-driven run (run_once), not only the
    inbox loop — otherwise the model call keeps running invisibly."""
    import pytest

    session, spec = _session(conn, fake_clock, repo)
    started = asyncio.Event()

    async def hang(messages, info):
        started.set()
        await asyncio.Event().wait()  # a model call that never returns

    ra = RunningAgent(session=session, spec=spec, model=FunctionModel(hang))
    session.registry.attach_running("a", ra)
    run = asyncio.create_task(ra.run_once("go"))
    await asyncio.wait_for(started.wait(), timeout=2)

    await ra.stop()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert session.registry.lifecycle("a") == "idle"
