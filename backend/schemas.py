"""Request bodies for the HTTP API (wire shapes only, no behavior).

Kept separate from ``models_domain`` (the persisted domain shapes) so the wire
format can evolve without touching the domain spine.
"""

from __future__ import annotations

from pydantic import BaseModel

from .models_domain import SessionMode, TeamGraph


class NewTaskRequest(BaseModel):
    prompt: str
    title: str = ""
    assigned_agent_id: str | None = None
    completion_signal: str = "self_reported"


class NewTeamRequest(BaseModel):
    name: str
    graph: TeamGraph | None = None


class RenameRequest(BaseModel):
    name: str


class ModeRequest(BaseModel):
    mode: SessionMode


class LaunchSessionRequest(BaseModel):
    team_id: str
    repo_path: str
    mode: SessionMode = "parallel"


class RunRequest(BaseModel):
    prompt: str


class AnswerRequest(BaseModel):
    """Answers for a pending ask_user call, aligned with its questions order."""

    answers: list[str]
