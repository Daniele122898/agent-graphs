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


async def test_stale_edit_hash_nudges_the_model_instead_of_killing_the_run(repo):
    """A recoverable tool error (stale/bogus edit hash) must surface as a
    retry prompt the model can act on — NOT a fatal exception. This is the
    bug that made every delegated edit take the target agent down."""
    (repo / "game.py").write_text("a\nb\nc\n")
    model = make_sequence_model(
        [
            # turn 1: edit with a hash someone else dictated (always stale)
            [ToolCallPart("edit_file", {"path": "game.py", "start_line": 1, "end_line": 1,
                                        "new_content": "x", "lines_hash": "deadbeef0000"})],
            # turn 2 (after the retry nudge): recover by re-reading
            [ToolCallPart("read_file", {"path": "game.py"})],
            # turn 3: report
            [TextPart("recovered after re-reading")],
        ]
    )
    dev = DevTools(repo, Capabilities.from_level("read-write"))
    agent = build_agent(AgentSpec(id="a", name="A"), model=model, dev_tools=dev)

    result = await agent.run("fix game.py", deps=AgentDeps())
    assert "recovered after re-reading" in result.output  # the run SURVIVED


async def test_sandbox_violation_is_also_a_nudge(repo):
    model = make_sequence_model(
        [
            [ToolCallPart("read_file", {"path": "../outside.txt"})],
            [TextPart("staying inside the repo")],
        ]
    )
    agent = build_agent(
        AgentSpec(id="a", name="A"), model=model,
        dev_tools=DevTools(repo, Capabilities.from_level("read")),
    )
    result = await agent.run("read something", deps=AgentDeps())
    assert "staying inside the repo" in result.output
