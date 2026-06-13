"""ask_user endpoints: list open questions, deliver the human's answers."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .. import wiring
from .schemas import AnswerRequest


def install(app: FastAPI) -> None:
    @app.get("/api/questions")
    def open_questions(session_id: str | None = None) -> dict:
        """All unanswered ask_user questions in this session (for page loads;
        live arrivals come over SSE as `user_question` events)."""
        session = wiring.resolve_session(app, session_id)
        return {"questions": session.harness.list_questions(session)}

    @app.post("/api/questions/{question_id}/answer")
    def answer_question(question_id: str, body: AnswerRequest, session_id: str | None = None) -> dict:
        """Resolve a pending ask_user call; the parked run resumes with these
        answers as the tool result."""
        session = wiring.resolve_session(app, session_id)
        try:
            ok = session.harness.answer_question(session, question_id, body.answers)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if not ok:
            raise HTTPException(404, "no such pending question (already answered, timed out, or the run ended)")
        return {"status": "answered", "id": question_id}
