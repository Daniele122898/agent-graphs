"""Snapshot/resume: after an agent runs and persists, a *fresh* SessionManager
(simulating a new process on the same DB) can rehydrate the session, and a
re-created agent continues with full prior history. In-flight tasks survive in
the DB across managers."""

from __future__ import annotations

from pydantic_ai.messages import TextPart

from backend.agent_state import AgentStateStore
from backend.models_domain import AgentSpec, Capabilities, GraphNode, TeamGraph
from backend.runtime import RunningAgent
from backend.sessions import SessionManager
from backend.tasks import TaskStore
from backend.teams import TeamStore
from tests.conftest import make_sequence_model


def _graph() -> TeamGraph:
    return TeamGraph(
        nodes=[GraphNode(spec=AgentSpec(id="lead", name="Lead", is_entry_point=True,
                                        capabilities=Capabilities.from_level("read")))]
    )


async def _settle(predicate, timeout=2.0):
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


async def test_resume_rehydrates_history(conn, fake_clock, repo):
    graph = _graph()
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    store = AgentStateStore(conn, clock=fake_clock)

    # --- session 1: run an agent, history is persisted ---
    mgr1 = SessionManager(conn, clock=fake_clock)
    s1 = mgr1.create_session(team_id=team.id, repo_path=repo, graph=graph)
    ra1 = RunningAgent(session=s1, spec=graph.nodes[0].spec, model=make_sequence_model([[TextPart("first answer")]]), state_store=store)
    s1.registry.attach_running("lead", ra1)
    ra1.start()
    ra1.submit("remember the number 7")
    await _settle(lambda: len(ra1.messages) >= 2 and store.get(s1.id, "lead") is not None)
    persisted_len = len(ra1.messages)
    await ra1.stop()

    # --- "new process": fresh manager on the same DB ---
    mgr2 = SessionManager(conn, clock=fake_clock)
    assert mgr2.get(s1.id) is None  # not in memory yet
    s2 = mgr2.resume_session(s1.id, graph)
    assert s2 is not None
    assert s2.repo_root == repo.resolve()
    assert s2.team_id == team.id

    # a re-created agent seeds from persisted history → continues with context
    ra2 = RunningAgent(
        session=s2,
        spec=graph.nodes[0].spec,
        model=make_sequence_model([[TextPart("second answer")]]),
        state_store=store,
        initial_messages=store.load_messages(s1.id, "lead"),
    )
    assert len(ra2.messages) == persisted_len  # rehydrated, not blank
    s2.registry.attach_running("lead", ra2)
    ra2.start()
    ra2.submit("what number?")
    await _settle(lambda: len(ra2.messages) > persisted_len)
    # history grew on top of the rehydrated prefix
    assert len(ra2.messages) > persisted_len
    await ra2.stop()


def test_tasks_survive_across_managers(conn, fake_clock, repo):
    graph = _graph()
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    mgr1 = SessionManager(conn, clock=fake_clock)
    s1 = mgr1.create_session(team_id=team.id, repo_path=repo, graph=graph)
    tasks = TaskStore(conn, clock=fake_clock)
    t = tasks.create(session_id=s1.id, title="X", prompt="do x", assigned_agent_id="lead")
    tasks.set_status(t.id, "running")  # in-flight

    # fresh manager + store on the same DB still sees the in-flight task
    tasks2 = TaskStore(conn, clock=fake_clock)
    resumed = SessionManager(conn, clock=fake_clock).resume_session(s1.id, graph)
    assert resumed is not None
    found = tasks2.list_for_session(s1.id)
    assert len(found) == 1 and found[0].status == "running"
