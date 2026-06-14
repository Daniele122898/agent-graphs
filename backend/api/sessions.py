"""Session endpoints: launch, resume, list, gateway mode."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from .. import wiring
from .schemas import LaunchSessionRequest, ModeRequest, RebindSessionRequest


def install(app: FastAPI) -> None:
    @app.get("/api/session")
    def current_session(session_id: str) -> dict:
        return wiring.resolve_session(app, session_id).info().model_dump()

    @app.get("/api/sessions")
    def list_sessions() -> dict:
        return {"sessions": [s.info().model_dump() for s in app.state.sessions.list()]}

    @app.post("/api/sessions")
    def launch_session(body: LaunchSessionRequest) -> dict:
        """Launch a new session: bind a team definition to a repo. Warns (does
        not block) if another active session already binds that repo — two task
        forces will fight over the same files."""
        team = app.state.teams.get(body.team_id)
        if team is None:
            raise HTTPException(404, f"no team '{body.team_id}'")
        existing = app.state.sessions.active_sessions_for_repo(body.repo_path)
        Path(body.repo_path).mkdir(parents=True, exist_ok=True)
        session = app.state.sessions.create_session(
            team_id=team.id, repo_path=body.repo_path, graph=team.graph, mode=body.mode,
            harness=body.harness,
        )
        info = session.info().model_dump()
        info["warning"] = (
            f"{len(existing)} other active session(s) already bound to this repo"
            if existing else None
        )
        return info

    @app.post("/api/sessions/{session_id}/resume")
    def resume_session(session_id: str) -> dict:
        """Rehydrate a persisted session into memory (snapshot/resume). The team
        graph is reloaded from its definition; per-agent history reloads lazily
        when each agent next runs."""
        live = app.state.sessions.get(session_id)
        if live is not None:
            return live.info().model_dump()
        row = app.state.conn.execute(
            "SELECT team_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "no such session")
        team = app.state.teams.get(row["team_id"])
        if team is None:
            raise HTTPException(409, f"team '{row['team_id']}' no longer exists; session cannot be resumed")
        session = app.state.sessions.resume_session(session_id, team.graph)
        if session is None:
            raise HTTPException(404, "no such session")
        return session.info().model_dump()

    @app.post("/api/session/mode")
    def set_mode(body: ModeRequest, session_id: str) -> dict:
        """Toggle the LLM execution gateway mode for this session: parallel
        (default) or serial (low-spec, one model call at a time)."""
        session = wiring.resolve_session(app, session_id)
        session.gateway.set_mode(body.mode)
        return session.info().model_dump()

    @app.post("/api/sessions/{session_id}/rebind")
    def rebind_session(session_id: str, body: RebindSessionRequest) -> dict:
        """Rebind a session to a different team."""
        team = app.state.teams.get(body.team_id)
        if team is None:
            raise HTTPException(404, f"no team '{body.team_id}'")
        session = app.state.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, f"no session '{session_id}'")
        # Update in-memory
        session.rebind(body.team_id, team.graph)
        # Persist to DB
        app.state.sessions._conn.execute(
            "UPDATE sessions SET team_id = ? WHERE id = ?",
            (body.team_id, session_id),
        )
        app.state.sessions._conn.commit()
        return session.info().model_dump()
