"""The streaming runner publishes the right events to the bus and performs real
work — driven deterministically with a scripted model (no tokens)."""

from __future__ import annotations

import asyncio
import json

from pydantic_ai.messages import TextPart, ToolCallPart

from backend.agents import build_agent
from backend.bus import EventBus
from backend.models_domain import AgentSpec, Capabilities
from backend.sessions import AgentRegistry
from backend.streaming import format_sse, run_agent_streamed
from backend.todos import AgentDeps
from backend.tools import DevTools
from tests.conftest import make_sequence_model


def test_format_sse_is_well_formed():
    frame = format_sse({"type": "text", "data": {"text": "hi"}, "session_id": "s"})
    assert frame.startswith("event: text\n")
    assert "data: " in frame
    assert frame.endswith("\n\n")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["data"]["text"] == "hi"


async def test_run_publishes_events_and_does_real_work(repo):
    bus = EventBus("s")
    reg = AgentRegistry()
    reg.register("a")

    events: list[dict] = []

    async def collect():
        async for e in bus.subscribe():
            events.append(e)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)  # ensure the subscriber is registered

    model = make_sequence_model(
        [
            [ToolCallPart("write_todos", {"todos": [{"content": "make a.txt", "status": "in_progress"}]})],
            [ToolCallPart("write_file", {"path": "a.txt", "content": "hi"})],
            [TextPart("done")],
        ]
    )
    agent = build_agent(
        AgentSpec(id="a", name="A"), model=model, dev_tools=DevTools(repo, Capabilities.from_level("read-write"))
    )
    out = await run_agent_streamed(
        bus=bus, registry=reg, agent_id="a", agent=agent, prompt="go", deps=AgentDeps(agent_id="a")
    )
    await asyncio.sleep(0.02)
    task.cancel()

    types = [e["type"] for e in events]
    assert types[0] == "agent_lifecycle"
    assert types[-1] == "agent_done"
    assert any(e["type"] == "tool_call" and e["data"]["tool"] == "write_file" for e in events)
    assert any(e["type"] == "tool_result" for e in events)
    assert any(e["type"] == "text" and e["data"]["text"] == "done" for e in events)
    # the todos event carries the live checklist
    todo_events = [e for e in events if e["type"] == "todos"]
    assert todo_events and todo_events[-1]["data"]["todos"][0]["content"] == "make a.txt"

    assert (repo / "a.txt").read_text() == "hi"
    assert reg.lifecycle("a") == "done"
    assert out == "done"


async def test_run_error_lands_agent_in_blocked(repo):
    bus = EventBus("s")
    reg = AgentRegistry()
    reg.register("a")
    events: list[dict] = []

    async def collect():
        async for e in bus.subscribe():
            events.append(e)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)

    # No write capability, but a tool that raises in the model script path:
    # force an error by making the model emit an invalid tool repeatedly is hard;
    # instead simulate a crash by passing a prompt and a model that raises.
    def boom(messages, info):
        raise RuntimeError("model exploded")

    from pydantic_ai.models.function import FunctionModel

    agent = build_agent(
        AgentSpec(id="a", name="A"),
        model=FunctionModel(boom),
        dev_tools=DevTools(repo, Capabilities.from_level("read")),
    )
    try:
        await run_agent_streamed(
            bus=bus, registry=reg, agent_id="a", agent=agent, prompt="go", deps=AgentDeps(agent_id="a")
        )
    except Exception:
        pass
    await asyncio.sleep(0.02)
    task.cancel()

    assert reg.lifecycle("a") == "blocked"
    assert any(e["type"] == "agent_error" for e in events)
