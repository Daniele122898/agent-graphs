"""Compaction: trims old turns while keeping recent ones and never orphaning a
tool-call/tool-return pair. Persona lives in instructions, so it's untouched."""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from backend.agents.history import compact_history


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_call(name: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args={}, tool_call_id="c1")])


def _tool_return(name: str) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name=name, content="ok", tool_call_id="c1")])


def test_no_compaction_below_threshold():
    msgs = [_user("hi"), _assistant("hello")]
    assert compact_history(msgs, max_messages=40) is msgs


def test_compaction_keeps_recent_user_anchored_window():
    # 10 user/assistant turns = 20 messages
    msgs = []
    for i in range(10):
        msgs.append(_user(f"q{i}"))
        msgs.append(_assistant(f"a{i}"))
    out = compact_history(msgs, max_messages=8, keep_last=4)
    assert len(out) < len(msgs)
    # the kept window starts at a user-prompt turn (clean boundary)
    assert isinstance(out[0], ModelRequest)
    assert any(isinstance(p, UserPromptPart) for p in out[0].parts)
    # most recent turn preserved
    assert out[-1] == msgs[-1]


def test_compaction_never_starts_on_orphaned_tool_return():
    # turn structure: user, tool_call, tool_return, assistant — repeated.
    msgs = []
    for i in range(8):
        msgs.append(_user(f"q{i}"))
        msgs.append(_tool_call("read_file"))
        msgs.append(_tool_return("read_file"))
        msgs.append(_assistant(f"a{i}"))
    out = compact_history(msgs, max_messages=8, keep_last=3)
    # whatever the window, it must begin at a user prompt, never a bare tool return
    assert isinstance(out[0], ModelRequest)
    assert any(isinstance(p, UserPromptPart) for p in out[0].parts)
    assert not any(isinstance(p, ToolReturnPart) for p in out[0].parts)
