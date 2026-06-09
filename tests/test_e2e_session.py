"""End-to-end spine test, no LLM.

Drives a whole session with FunctionModel: a task enters → the lead lays out
todos → delegates to an expert (ask_agent) → writes a real file → a reviewer
gate approves → the task reaches `done`. Asserts on REAL filesystem changes and
REAL task-state/message transitions. This is the test that proves the system
works end to end; everything underneath (sandbox, toolset, delegation, gates,
state machine) is wired correctly.
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from backend.a2a import MessageLog
from backend.models_domain import AgentSpec, Capabilities, GraphEdge, GraphNode, TeamGraph
from backend.runtime import RunningAgent
from backend.sessions import SessionManager
from backend.tasks import ReviewVerdict, TaskRunner, TaskStore
from backend.teams import TeamStore
from tests.conftest import make_sequence_model


def _graph() -> TeamGraph:
    lead = AgentSpec(
        id="lead", name="Lead", is_entry_point=True,
        model="test:lead", capabilities=Capabilities.from_level("read-write"),
    )
    expert = AgentSpec(id="expert", name="Expert", model="test:expert", capabilities=Capabilities.from_level("read"))
    senior = AgentSpec(id="senior", name="Senior", model="test:senior", capabilities=Capabilities.from_level("read"))
    return TeamGraph(
        nodes=[GraphNode(spec=lead), GraphNode(spec=expert), GraphNode(spec=senior)],
        edges=[GraphEdge(id="e1", source="lead", target="expert", label="naming advice")],
    )


async def test_task_flows_to_done_through_delegation_and_review(conn, fake_clock, repo):
    graph = _graph()
    team = TeamStore(conn, clock=fake_clock).create("T", graph)
    session = SessionManager(conn, clock=fake_clock).create_session(
        team_id=team.id, repo_path=repo, graph=graph
    )
    msg_log = MessageLog(conn, clock=fake_clock)
    tasks = TaskStore(conn, clock=fake_clock)

    # The expert answers a naming question in one turn.
    expert_model = make_sequence_model([[TextPart("call it result.txt")]])

    def model_resolver(model_str: str):
        return {"test:expert": expert_model}.get(model_str, expert_model)

    # The lead: plan → consult expert → write the file → report done.
    lead_model = make_sequence_model(
        [
            [ToolCallPart("write_todos", {"todos": [
                {"content": "ask expert for filename", "status": "in_progress"},
                {"content": "write the file", "status": "pending"},
            ]})],
            [ToolCallPart("ask_agent", {"target_id": "expert", "question": "what should I name the file?"})],
            [ToolCallPart("write_file", {"path": "result.txt", "content": "hello from the team\n"})],
            [TextPart("done — created result.txt as advised")],
        ]
    )
    lead_ra = RunningAgent(
        session=session,
        spec=next(n.spec for n in graph.nodes if n.spec.id == "lead"),
        model=lead_model,
        message_log=msg_log,
        model_resolver=model_resolver,
    )
    session.registry.attach_running("lead", lead_ra)

    # A reviewer agent with structured ReviewVerdict output that approves.
    def reviewer_fn(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"approved": True, "critique": "looks good"})])

    async def run_agent(agent_id, prompt):
        return await lead_ra.run_once(prompt)

    async def run_reviewer(reviewer_id, task_prompt, result):
        agent = Agent(model=FunctionModel(reviewer_fn), output_type=ReviewVerdict)
        r = await agent.run(f"{task_prompt}\n{result}")
        return r.output

    runner = TaskRunner(
        tasks,
        run_agent=run_agent,
        run_reviewer=run_reviewer,
        run_check=lambda c: (0, ""),
        publish=session.bus.publish,
    )

    task = tasks.create(
        session_id=session.id, title="Make the file", prompt="Create the result file with the team's help.",
        assigned_agent_id="lead", completion_signal="reviewer:senior",
    )
    final = await runner.run(task.id)

    # 1. real filesystem change
    assert (repo / "result.txt").read_text() == "hello from the team\n"
    # 2. real delegation happened and was logged
    logged = msg_log.for_session(session.id)
    assert any(m["from_agent"] == "lead" and m["to_agent"] == "expert" and m["kind"] == "question" for m in logged)
    assert any(m["from_agent"] == "expert" and m["to_agent"] == "lead" and m["kind"] == "reply" for m in logged)
    # 3. task reached done through the reviewer gate
    assert final == "done"
    assert tasks.get(task.id).status == "done"
