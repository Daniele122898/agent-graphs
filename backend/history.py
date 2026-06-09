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
