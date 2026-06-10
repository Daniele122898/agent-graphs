"""The ``write_todos`` tool + the per-agent dependency object.

Progress tracking is the industry-standard primitive: a todo list the agent
maintains as a tool call (``pending``/``in_progress``/``completed``). It's
mostly a rendering+logging concern, not an engine — the agent keeps its own
plan, and the UI renders it live.

``AgentDeps`` is the dependency object injected into every agent run. It holds
the live todo list and identity (session/agent) so tools and history processors
can reach session-scoped services later. Kept tiny on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext

from .models_domain import Todo


@dataclass
class AgentDeps:
    session_id: str = ""
    agent_id: str = ""
    todos: list[Todo] = field(default_factory=list)
    # Delegation wiring (Phase 4). ``delegator`` is a a2a.Delegator, kept untyped
    # here to avoid a circular import. ``delegation_chain`` is the agents already
    # visited in this delegation path, for cycle/depth guards.
    delegator: Any = None
    delegation_chain: list[str] = field(default_factory=list)
    # questions.QuestionBoard (untyped to avoid a circular import): the
    # session-owned registry the ask_user tool parks runs on.
    question_board: Any = None


def write_todos(ctx: RunContext[AgentDeps], todos: list[Todo]) -> str:
    """Replace the current todo checklist. Call this before complex or delegated
    work to lay out a plan, and update item statuses as you go. (Skip it for
    trivial work of fewer than ~3 steps — the 3-task rule.)
    """
    ctx.deps.todos = list(todos)
    pending = sum(1 for t in todos if t.status != "completed")
    return f"todos updated: {len(todos)} items, {pending} not yet completed"


def all_completed(todos: list[Todo]) -> bool:
    """True if every todo is completed (or the list is empty). Used by the task
    system (Phase 5) to refuse completion while todos remain pending."""
    return all(t.status == "completed" for t in todos)
