"""Streaming: run an agent and publish its work to the session bus; expose that
bus as Server-Sent Events for the Agent tab.

We drive the run with ``agent.iter()`` (node-by-node) rather than
``run_stream_events`` (token deltas) because ``iter`` works with plain
``FunctionModel`` — so the runner is exercised deterministically in tests with
zero tokens. Node-level granularity (tool calls, tool results, text, thinking,
todos, done) is what the Agent tab needs; token-delta streaming for real models
is an additive enhancement, not a prerequisite.

The model is whatever was injected into the agent (resolved from the spec for
real runs, a ``FunctionModel`` in tests) — this module never constructs one.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    TextPart,
    ThinkingPart,
)

from .bus import EventBus
from .todos import AgentDeps

if TYPE_CHECKING:
    from .sessions import AgentRegistry
    from .stats import UsageTally


def format_sse(event: dict) -> str:
    """Format one bus event as an SSE frame. Pure."""
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


async def sse_stream(bus: EventBus) -> AsyncIterator[str]:
    """Yield SSE frames for everything published to the bus until the client
    disconnects (the generator is closed)."""
    async for event in bus.subscribe():
        yield format_sse(event)


def _todos_payload(deps: AgentDeps) -> list[dict]:
    return [t.model_dump() for t in deps.todos]


async def run_agent_streamed(
    *,
    bus: EventBus,
    registry: "AgentRegistry",
    agent_id: str,
    agent: Agent[AgentDeps],
    prompt: str,
    deps: AgentDeps,
    usage_tally: "UsageTally | None" = None,
    message_history: list[ModelMessage] | None = None,
    history_out: list[ModelMessage] | None = None,
    usage=None,
    usage_limits=None,
) -> str:
    """Run an agent to completion, publishing its work to the bus and driving the
    agent's lifecycle. Returns the final output text.

    ``message_history`` continues a prior conversation (so follow-ups and
    interjections build on context). If ``history_out`` is provided, it is
    replaced in-place with the full message list after the run, so the caller
    (a ``RunningAgent``) can persist and continue.

    ``usage``/``usage_limits`` support delegated runs: a parent run passes its
    own ``RunUsage`` so the child's consumption counts against the same budget.
    The per-agent tally is then credited with only this run's *delta*, not the
    shared accumulated totals.

    On error, the agent lands in ``blocked`` (the user's attention) and an
    ``agent_error`` event is published — never a silent failure.
    """
    # Echo the prompt so it appears in the Agent tab transcript (the UI renders
    # this as the user's own chat bubble).
    bus.publish("user_message", {"agent_id": agent_id, "text": prompt})
    registry.set_lifecycle(agent_id, "running")
    bus.publish("agent_lifecycle", {"agent_id": agent_id, "lifecycle": "running"})
    base = (
        (usage.requests, usage.input_tokens or 0, usage.output_tokens or 0)
        if usage is not None
        else (0, 0, 0)
    )
    try:
        async with agent.iter(
            prompt, deps=deps, message_history=message_history, usage=usage, usage_limits=usage_limits
        ) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    bus.publish("model_request", {"agent_id": agent_id})
                elif Agent.is_call_tools_node(node):
                    for part in node.model_response.parts:
                        if isinstance(part, ThinkingPart) and part.content:
                            bus.publish("thinking", {"agent_id": agent_id, "text": part.content})
                        elif isinstance(part, TextPart) and part.content:
                            bus.publish("text", {"agent_id": agent_id, "text": part.content})
                    async with node.stream(run.ctx) as tool_stream:
                        async for ev in tool_stream:
                            if isinstance(ev, FunctionToolCallEvent):
                                bus.publish(
                                    "tool_call",
                                    {
                                        "agent_id": agent_id,
                                        "tool": ev.part.tool_name,
                                        "args": _jsonable(ev.part.args),
                                    },
                                )
                            elif isinstance(ev, FunctionToolResultEvent):
                                bus.publish(
                                    "tool_result",
                                    {
                                        "agent_id": agent_id,
                                        "tool": getattr(ev.part, "tool_name", ""),
                                        "result": str(getattr(ev.part, "content", "")),
                                    },
                                )
                    # Publish the current checklist after each step so the Agent
                    # tab's todo list stays live.
                    bus.publish("todos", {"agent_id": agent_id, "todos": _todos_payload(deps)})
            output = run.result.output if run.result else ""
            if run.result is not None:
                if usage_tally is not None:
                    u = run.result.usage
                    usage_tally.add(
                        agent_id,
                        requests=u.requests - base[0],
                        input_tokens=(u.input_tokens or 0) - base[1],
                        output_tokens=(u.output_tokens or 0) - base[2],
                    )
                if history_out is not None:
                    history_out[:] = run.result.all_messages()
        registry.set_lifecycle(agent_id, "done")
        bus.publish("agent_done", {"agent_id": agent_id, "output": str(output)})
        return str(output)
    except Exception as e:  # noqa: BLE001 — surface, don't swallow
        registry.set_lifecycle(agent_id, "blocked")
        bus.publish("agent_error", {"agent_id": agent_id, "error": str(e)})
        raise


def _jsonable(args: str | dict | None) -> object:
    """Tool-call args may be a dict or a JSON string; normalize for the wire."""
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return args
    return args
