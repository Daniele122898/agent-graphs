"""Team-library endpoints: the reusable definitions (templates)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .. import wiring
from ..domain.graph import validate_structure
from ..domain.models import Team, TeamGraph
from .schemas import NewTeamRequest, UpdateTeamRequest


def _team_summary(team: Team) -> dict:
    """The shared list/create/update wire shape: metadata + agent count, WITHOUT
    the full graph (fetched on demand via /teams/{id}/graph). One shape across all
    three so the frontend `TeamRow` is honest everywhere."""
    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "agent_count": len(team.graph.nodes),
    }


def install(app: FastAPI) -> None:
    @app.get("/api/teams")
    def list_teams() -> dict:
        return {"teams": [_team_summary(t) for t in app.state.teams.list()]}

    @app.post("/api/teams")
    def create_team(body: NewTeamRequest) -> dict:
        # A brand-new team gets a starter lead agent (so it's launchable) unless
        # an explicit graph was supplied (e.g. "save as" from the editor).
        graph = body.graph if body.graph is not None else wiring.starter_team_graph()
        errors = validate_structure(graph)
        if errors:
            raise HTTPException(422, {"errors": errors})
        return _team_summary(app.state.teams.create(body.name, graph, description=body.description))

    @app.get("/api/teams/{team_id}")
    def get_team(team_id: str) -> dict:
        team = app.state.teams.get(team_id)
        if team is None:
            raise HTTPException(404, "no such team")
        return team.model_dump()

    @app.get("/api/teams/{team_id}/graph")
    def get_team_graph(team_id: str) -> dict:
        team = app.state.teams.get(team_id)
        if team is None:
            raise HTTPException(404, "no such team")
        return team.graph.model_dump()

    @app.put("/api/teams/{team_id}/graph")
    async def put_team_graph(team_id: str, graph: TeamGraph) -> dict:
        # async (event loop, not a threadpool thread) so the live session's
        # `session.graph` swap is serialized with the harness reads of it
        # (opencode's _ensure reads session.graph mid-run); a sync handler ran
        # this on a worker thread, racing those reads.
        return wiring.apply_team_graph(app, team_id, graph)

    @app.patch("/api/teams/{team_id}")
    def update_team(team_id: str, body: UpdateTeamRequest) -> dict:
        # Metadata only (name / description) — neither is read by a live run, so
        # no busy-guard is needed (unlike the graph PUT). Partial: omitted fields
        # are left unchanged.
        team = app.state.teams.update_meta(
            team_id, name=body.name, description=body.description
        )
        if team is None:
            raise HTTPException(404, "no such team")
        return _team_summary(team)

    @app.delete("/api/teams/{team_id}")
    def delete_team(team_id: str) -> dict:
        # Block-if-bound: a team a session is bound to cannot be deleted, else
        # that session is orphaned (resume after restart 404s — the team is
        # gone). The sessions table is the source of truth (SessionManager owns
        # it), so this catches persisted-but-not-loaded sessions too. The user
        # rebinds or removes those sessions first.
        if app.state.teams.get(team_id) is None:
            raise HTTPException(404, "no such team")
        bound = app.state.sessions.sessions_using_team(team_id)
        if bound:
            names = ", ".join(f"{Path(s.repo_path).name} · {s.mode}" for s in bound)
            raise HTTPException(
                409,
                f"team is in use by {len(bound)} session(s): {names}. "
                "Rebind or close them first.",
            )
        try:
            app.state.teams.delete(team_id)
        except sqlite3.IntegrityError:
            # A session was bound in the (single-user, narrow) race between the
            # check above and here; the FK constraint refused the delete — the
            # CORRECT outcome (it can't orphan a session). Report it as the same
            # 409, not an uncaught 500.
            raise HTTPException(409, "team is now in use by a session. Rebind or close it first.")
        return {"status": "deleted", "team_id": team_id}
