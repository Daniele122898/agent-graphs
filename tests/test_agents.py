"""Phase 2 wiring: an injected (scripted) model drives a built agent through its
capability-derived toolset to make real filesystem changes, and the todo list is
captured. This proves the whole harness end-to-end without an LLM."""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from backend.agents import build_agent
from backend.models_domain import AgentSpec, Capabilities
from backend.todos import AgentDeps
from backend.tools import DevTools
from tests.conftest import make_sequence_model


async def test_agent_writes_repo_file_and_records_todos(repo):
    model = make_sequence_model(
        [
            # turn 1: lay out a plan
            [ToolCallPart("write_todos", {"todos": [{"content": "write hello.py", "status": "in_progress"}]})],
            # turn 2: actually write the file
            [ToolCallPart("write_file", {"path": "hello.py", "content": 'print("hi")\n'})],
            # turn 3: report done
            [TextPart("created hello.py")],
        ]
    )
    dev = DevTools(repo, Capabilities.from_level("read-write"))
    agent = build_agent(AgentSpec(id="a", name="A"), model=model, dev_tools=dev)

    deps = AgentDeps(session_id="s", agent_id="a")
    result = await agent.run("make a hello file", deps=deps)

    assert (repo / "hello.py").read_text() == 'print("hi")\n'
    assert "created hello.py" in result.output
    assert [t.content for t in deps.todos] == ["write hello.py"]


async def test_model_request_carries_identity_capabilities_and_environment(repo):
    """What the model actually receives: persona, capability limits, and the
    run environment (repo root, OS, date) must all be in the request's
    instructions — agents plan within known limits instead of discovering them
    through failed tool calls."""
    captured: dict = {}

    def fn(messages, info):
        captured["instructions"] = messages[0].instructions
        return ModelResponse(parts=[TextPart("ok")])

    caps = Capabilities(filesystem="read", read_paths=["src/**"], write_paths=[], bash=False)
    spec = AgentSpec(id="ro", name="Reviewer", persona="You review code.", capabilities=caps)
    agent = build_agent(spec, model=FunctionModel(fn), dev_tools=DevTools(repo, caps))
    await agent.run("hi", deps=AgentDeps(agent_id="ro"))

    instr = captured["instructions"]
    assert "You review code." in instr                       # persona
    assert "read-only" in instr and "src/**" in instr        # capability summary
    assert "no bash tool" in instr
    assert str(Path(repo).resolve()) in instr                # repo root
    assert "Today's date" in instr                           # fresh environment


async def test_read_only_agent_cannot_write(repo):
    """The model tries to write, but a read-only agent's toolset has no
    write_file — the call is rejected and the file never appears."""
    model = make_sequence_model(
        [
            [ToolCallPart("write_file", {"path": "x.py", "content": "nope"})],
            [TextPart("could not write")],
        ]
    )
    caps = Capabilities(filesystem="read", read_paths=["**"], write_paths=[], bash=False)
    agent = build_agent(AgentSpec(id="ro", name="RO"), model=model, dev_tools=DevTools(repo, caps))

    result = await agent.run("write a file", deps=AgentDeps())
    assert not (repo / "x.py").exists()
    assert "could not write" in result.output
