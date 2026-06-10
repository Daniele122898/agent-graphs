"""ask_user — a structured channel for an agent to ask the human.

A run that needs information must not end its turn with questions written as
plain text: nothing feeds an answer back, so the run just dies and the work
stalls (the failure mode this module exists to remove). Instead the agent
calls the ``ask_user`` tool, which parks the run on an ``asyncio.Future`` and
publishes the questions to the control room; the user answers (a choice or
free text per question) via ``POST /api/questions/{id}/answer``, and the
answers become the tool's return value — the run continues with them.

Pending questions live on the ``QuestionBoard`` owned by each ``Session``
(per-session ownership, like the bus/registry). They are in-memory only: a
process restart cancels them together with the run that asked, and the task
system's orphan-parking covers the fallout.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext

from .todos import AgentDeps
from .util import iso_now, new_id

if TYPE_CHECKING:  # avoid a circular import with sessions.py
    from .sessions import Session

ANSWER_TIMEOUT = float(os.environ.get("AGENT_GRAPHS_ASK_USER_TIMEOUT", "3600"))
"""How long a run waits for the human before giving up (default: 1 hour)."""


class UserQuestion(BaseModel):
    """One question for the human. ``options`` offers multiple choice; the UI
    always allows a free-form answer as well."""

    question: str
    options: list[str] = []


@dataclass
class PendingQuestion:
    id: str
    agent_id: str
    questions: list[UserQuestion]
    created_at: str
    future: "asyncio.Future[list[str]]"


class QuestionBoard:
    """Per-session registry of unanswered ``ask_user`` calls."""

    def __init__(self, session: "Session"):
        self._session = session
        self._open: dict[str, PendingQuestion] = {}

    async def ask(self, agent_id: str, questions: list[UserQuestion]) -> str:
        """Publish the questions, park the run until answered (or timed out),
        and return the answers formatted for the model."""
        pq = PendingQuestion(
            id=new_id("q_"),
            agent_id=agent_id,
            questions=questions,
            created_at=iso_now(),
            future=asyncio.get_event_loop().create_future(),
        )
        self._open[pq.id] = pq
        self._set_lifecycle(agent_id, "waiting-on-user")
        self._session.bus.publish("user_question", self._payload(pq))
        try:
            answers = await asyncio.wait_for(pq.future, timeout=ANSWER_TIMEOUT)
        except asyncio.TimeoutError:
            return (
                f"[the user did not answer within {int(ANSWER_TIMEOUT)}s — proceed on your "
                "best judgment and note the open question in your final answer]"
            )
        finally:
            self._open.pop(pq.id, None)
            self._set_lifecycle(agent_id, "running")
            self._session.bus.publish("user_question_done", {"id": pq.id, "agent_id": agent_id})
        lines = ["The user answered your questions:"]
        for q, a in zip(questions, answers):
            lines.append(f"- Q: {q.question}\n  A: {a}")
        return "\n".join(lines)

    def answer(self, question_id: str, answers: list[str]) -> bool:
        """Resolve a pending question with the user's answers. False if the
        question is unknown (already answered, timed out, or the run died)."""
        pq = self._open.get(question_id)
        if pq is None or pq.future.done():
            return False
        if len(answers) != len(pq.questions):
            raise ValueError(f"expected {len(pq.questions)} answers, got {len(answers)}")
        pq.future.set_result(list(answers))
        return True

    def list_open(self) -> list[dict]:
        return [self._payload(pq) for pq in self._open.values()]

    def _payload(self, pq: PendingQuestion) -> dict:
        return {
            "id": pq.id,
            "agent_id": pq.agent_id,
            "questions": [q.model_dump() for q in pq.questions],
            "created_at": pq.created_at,
        }

    def _set_lifecycle(self, agent_id: str, lifecycle: str) -> None:
        self._session.registry.set_lifecycle(agent_id, lifecycle)  # type: ignore[arg-type]
        self._session.bus.publish("agent_lifecycle", {"agent_id": agent_id, "lifecycle": lifecycle})


async def ask_user(ctx: RunContext[AgentDeps], questions: list[UserQuestion]) -> str:
    """Ask the human one or more questions and WAIT for their answers. Use this
    whenever you need a decision, preference, or missing information — never
    end your turn with questions written as plain text (the user cannot answer
    those). Offer ``options`` for multiple choice where sensible; the user can
    always answer in free text.
    """
    board = ctx.deps.question_board
    if board is None:
        raise ModelRetry("ask_user is not available in this context; decide on your best judgment.")
    if not questions:
        raise ModelRetry("ask_user needs at least one question.")
    return await board.ask(ctx.deps.agent_id, questions)
