"""Request bodies for the HTTP API (wire shapes only, no behavior).

Kept separate from ``domain/models.py`` (the persisted domain shapes) so the wire
format can evolve without touching the domain spine.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..domain.models import SessionMode, TeamGraph


class NewTaskRequest(BaseModel):
    prompt: str
    title: str = ""
    assigned_agent_id: str | None = None
    completion_signal: str = "self_reported"
    timeout_hours: float = 1.0  # per-task wall-clock budget in hours (0 = no limit)


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
    harness: str | None = None  # native | opencode (default from config)


class AskAgentInternalRequest(BaseModel):
    """Body of the OpenCode ask_agent callback (see harness/opencode/config.py).
    Localhost-only, authenticated by a per-session token header."""

    session_id: str
    asker_id: str
    target_id: str
    question: str


class TeamAssignmentWire(BaseModel):
    """One (teammate, task) entry of an ask_team fan-out callback."""

    target_id: str
    task: str


class AskTeamInternalRequest(BaseModel):
    """Body of the OpenCode ask_team callback: fan work out to several teammates
    at once. Localhost-only, authenticated by the per-session token header."""

    session_id: str
    asker_id: str
    assignments: list[TeamAssignmentWire]


class RunRequest(BaseModel):
    prompt: str


class AnswerRequest(BaseModel):
    """Answers for a pending ask_user call, aligned with its questions order."""

    answers: list[str]
