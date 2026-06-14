"""Team-library endpoints: the reusable definitions (templates)."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .. import wiring
from ..domain.graph import validate_structure
from ..domain.models import TeamGraph
from .schemas import NewTeamRequest, RenameRequest


def install(app: FastAPI) -> None:
    @app.get("/api/teams")
    def list_teams() -> dict:
        return {"teams": [t.model_dump() for t in app.state.teams.list()]}

    @app.post("/api/teams")
    def create_team(body: NewTeamRequest) -> dict:
        # A brand-new team gets a starter lead agent (so it's launchable) unless
        # an explicit graph was supplied (e.g. "save as" from the editor).
        graph = body.graph if body.graph is not None else wiring.starter_team_graph()
        errors = validate_structure(graph)
        if errors:
            raise HTTPException(422, {"errors": errors})
        return app.state.teams.create(body.name, graph).model_dump()

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

    @app.post("/api/teams/{team_id}/rename")
    def rename_team(team_id: str, body: RenameRequest) -> dict:
        team = app.state.teams.rename(team_id, body.name)
        if team is None:
            raise HTTPException(404, "no such team")
        return team.model_dump()
