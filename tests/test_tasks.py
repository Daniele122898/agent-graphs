"""Task system: state machine, store round-trip, and the runner's completion
gates + safety rails — driven with injected fakes (no models/subprocess)."""

from __future__ import annotations

import pytest

from backend.domain.models import AgentSpec, GraphNode, TeamGraph
from backend.runtime.sessions import SessionManager
from backend.runtime.tasks import (
    ReviewVerdict,
    TaskRunner,
    TaskStore,
    parse_completion_signal,
    validate_transition,
)
from backend.storage.teams import TeamStore


# --- pure state machine -----------------------------------------------------


def test_legal_and_illegal_transitions():
    assert validate_transition("queued", "running")
    assert validate_transition("running", "needs_review")
    assert validate_transition("needs_review", "done")
    assert validate_transition("needs_review", "needs_revision")
    assert validate_transition("needs_revision", "running")
    assert not validate_transition("done", "running")  # terminal
    assert not validate_transition("queued", "done")  # must pass through running


def test_parse_completion_signal():
    assert parse_completion_signal("self_reported") == ("self_reported", "")
    assert parse_completion_signal("reviewer:senior") == ("reviewer", "senior")
    assert parse_completion_signal("check:pytest -q") == ("check", "pytest -q")


# --- store ------------------------------------------------------------------


def _session(conn, fake_clock):
    team = TeamStore(conn, clock=fake_clock).create(
        "T", TeamGraph(nodes=[GraphNode(spec=AgentSpec(id="lead", name="Lead", is_entry_point=True))])
    )
    return SessionManager(conn, clock=fake_clock).create_session(
        team_id=team.id, repo_path="/tmp/x", graph=team.graph
    )


def test_task_round_trips_through_store(conn, fake_clock):
    session = _session(conn, fake_clock)
    store = TaskStore(conn, clock=fake_clock)
    t = store.create(
        session_id=session.id, title="Add auth", prompt="implement login",
        assigned_agent_id="lead", completion_signal="check:pytest",
    )
    got = store.get(t.id)
    assert got is not None
    assert got.title == "Add auth"
    assert got.completion_signal == "check:pytest"
    assert got.status == "queued"
    assert store.list_for_session(session.id)[0].id == t.id


# --- runner: completion gates ----------------------------------------------


def _store_with_task(conn, fake_clock, signal):
    session = _session(conn, fake_clock)
    store = TaskStore(conn, clock=fake_clock)
    task = store.create(
        session_id=session.id, title="T", prompt="do the thing",
        assigned_agent_id="lead", completion_signal=signal,
    )
    return store, task


async def _noop_reviewer(_id, _p, _r):
    return ReviewVerdict(approved=True)


async def test_self_reported_goes_straight_to_done(conn, fake_clock):
    store, task = _store_with_task(conn, fake_clock, "self_reported")

    async def agent(_id, prompt):
        return "did it"

    runner = TaskRunner(store, run_agent=agent, run_reviewer=_noop_reviewer, run_check=lambda c: (0, ""))
    assert await runner.run(task.id) == "done"
    assert store.get(task.id).status == "done"
    assert store.get(task.id).result == "did it"


async def test_check_gate_passes_then_done(conn, fake_clock):
    store, task = _store_with_task(conn, fake_clock, "check:pytest")
    calls = {"n": 0}

    async def agent(_id, prompt):
        return "implemented"

    def check(cmd):
        calls["n"] += 1
        return (0, "ok")  # passes

    runner = TaskRunner(store, run_agent=agent, run_reviewer=_noop_reviewer, run_check=check)
    assert await runner.run(task.id) == "done"
    assert calls["n"] == 1


async def test_check_failure_triggers_revision_then_recovers(conn, fake_clock):
    store, task = _store_with_task(conn, fake_clock, "check:pytest")
    agent_prompts: list[str] = []
    check_results = iter([(1, "1 failed"), (0, "ok")])  # fail once, then pass

    async def agent(_id, prompt):
        agent_prompts.append(prompt)
        return "attempt"

    runner = TaskRunner(store, run_agent=agent, run_reviewer=_noop_reviewer, run_check=lambda c: next(check_results))
    assert await runner.run(task.id) == "done"
    # ran twice; the second prompt carried the failure feedback
    assert len(agent_prompts) == 2
    assert "failed" in agent_prompts[1]


async def test_check_keeps_failing_lands_in_blocked(conn, fake_clock):
    store, task = _store_with_task(conn, fake_clock, "check:pytest")

    async def agent(_id, prompt):
        return "attempt"

    runner = TaskRunner(
        store, run_agent=agent, run_reviewer=_noop_reviewer, run_check=lambda c: (1, "still failing"),
        max_revision_rounds=2,
    )
    assert await runner.run(task.id) == "blocked"
    assert store.get(task.id).status == "blocked"


async def test_reviewer_rejection_then_approval(conn, fake_clock):
    store, task = _store_with_task(conn, fake_clock, "reviewer:senior")
    verdicts = iter([ReviewVerdict(approved=False, critique="missing tests"), ReviewVerdict(approved=True)])
    prompts: list[str] = []

    async def agent(_id, prompt):
        prompts.append(prompt)
        return "work"

    async def reviewer(_id, _p, _r):
        return next(verdicts)

    runner = TaskRunner(store, run_agent=agent, run_reviewer=reviewer, run_check=lambda c: (0, ""))
    assert await runner.run(task.id) == "done"
    assert "missing tests" in prompts[1]


async def test_agent_error_parks_task_in_blocked(conn, fake_clock):
    store, task = _store_with_task(conn, fake_clock, "self_reported")

    async def agent(_id, prompt):
        raise RuntimeError("turn cap hit")

    runner = TaskRunner(store, run_agent=agent, run_reviewer=_noop_reviewer, run_check=lambda c: (0, ""))
    assert await runner.run(task.id) == "blocked"
    assert "turn cap hit" in store.get(task.id).result


async def test_cancellation_mid_run_parks_the_task_blocked(conn, fake_clock):
    """Stop on the assigned agent cancels the run; the task must land in
    blocked (where Retry revives it), never stay 'running' forever."""
    import asyncio

    store, task = _store_with_task(conn, fake_clock, "self_reported")
    started = asyncio.Event()

    async def agent(_id, _prompt):
        started.set()
        await asyncio.Event().wait()  # a run that never finishes on its own

    runner = TaskRunner(store, run_agent=agent, run_reviewer=_noop_reviewer, run_check=lambda c: (0, ""))
    t = asyncio.create_task(runner.run(task.id))
    await asyncio.wait_for(started.wait(), timeout=2)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t

    got = store.get(task.id)
    assert got.status == "blocked"
    assert "Retry" in got.result
