"""Compaction: keep an agent's message history from growing unbounded.

The persona lives in Pydantic AI ``instructions`` (sticky, re-inserted every
request), so compaction never touches it — it only trims the *conversation*.

v1 is a deterministic **slice-recent** processor: once history exceeds a
threshold, keep only the most recent turns. The one subtlety is correctness:
never start the kept window in the middle of a tool-call/tool-return pair, or
the model sees an orphaned tool result. So the cut is made at a clean boundary —
a ``ModelRequest`` that carries a real ``UserPromptPart`` (a fresh turn), never
one that is only tool returns. If no clean boundary exists in the tail window,
history is left intact (correctness over aggressiveness).

A summarize-oldest variant (one model call to compress dropped turns) is a
future option; slice-recent is chosen first because it's deterministic and
needs no extra model call.
"""

from __future__ import annotations

from pydantic_ai.capabilities.process_history import ProcessHistory
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

DEFAULT_MAX_MESSAGES = 40
DEFAULT_KEEP_LAST = 20


def compact_history(
    messages: list[ModelMessage],
    *,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    keep_last: int = DEFAULT_KEEP_LAST,
) -> list[ModelMessage]:
    """Return a trimmed history (pure). No-op below ``max_messages``."""
    if len(messages) <= max_messages:
        return messages
    # Find the earliest clean cut at/after the tail window: a user-prompt turn.
    window_start = len(messages) - keep_last
    for i in range(window_start, len(messages)):
        m = messages[i]
        if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts):
            return messages[i:]
    return messages  # no safe boundary found — don't risk orphaning tool pairs


def compaction_capability(
    *, max_messages: int = DEFAULT_MAX_MESSAGES, keep_last: int = DEFAULT_KEEP_LAST
) -> ProcessHistory:
    """Build the Pydantic AI capability that runs ``compact_history`` before each
    model request."""

    def processor(messages: list[ModelMessage]) -> list[ModelMessage]:
        return compact_history(messages, max_messages=max_messages, keep_last=keep_last)

    return ProcessHistory(processor)


# --- rendering (for the control room) ----------------------------------------

def render_messages(messages: list[ModelMessage]) -> list[dict]:
    """Flatten stored messages into renderable rows for the UI — the same
    shapes the live SSE events use, so the Agent tab renders past and live
    work identically. This is *exactly* the conversation a run resumes with
    (instructions are separate: sticky, rebuilt every request)."""
    from pydantic_ai.messages import (
        ModelResponse,
        RetryPromptPart,
        SystemPromptPart,
        TextPart,
        ThinkingPart,
        ToolCallPart,
        ToolReturnPart,
    )

    rows: list[dict] = []
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart):
                    text = p.content if isinstance(p.content, str) else str(p.content)
                    rows.append({"kind": "user", "text": text})
                elif isinstance(p, ToolReturnPart):
                    rows.append({"kind": "tool_result", "tool": p.tool_name, "text": str(p.content)})
                elif isinstance(p, RetryPromptPart):
                    rows.append({"kind": "retry", "text": str(p.content)})
                elif isinstance(p, SystemPromptPart):
                    rows.append({"kind": "system", "text": p.content})
        elif isinstance(m, ModelResponse):
            for p in m.parts:
                if isinstance(p, ThinkingPart):
                    if p.content:
                        rows.append({"kind": "thinking", "text": p.content})
                elif isinstance(p, TextPart):
                    if p.content:
                        rows.append({"kind": "text", "text": p.content})
                elif isinstance(p, ToolCallPart):
                    rows.append({"kind": "tool_call", "tool": p.tool_name, "args": p.args_as_dict()})
    return rows
