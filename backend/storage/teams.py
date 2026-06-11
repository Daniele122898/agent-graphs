"""TeamStore — CRUD over the ``teams`` table.

A team is a reusable, repo-agnostic *definition* (template): the graph topology
plus each agent's persona/capabilities/model/links. This store owns the
serialization of the ``TeamGraph`` to/from JSON and nothing about running
sessions (that's ``runtime/sessions.py``).

The clock is injected so timestamps are deterministic in tests.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

from ..domain.models import Team, TeamGraph
from ..util import iso_now, new_id


class TeamStore:
    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], str] = iso_now):
        self._conn = conn
        self._now = clock

    def create(self, name: str, graph: TeamGraph | None = None) -> Team:
        team = Team(
            id=new_id("team_"),
            name=name,
            graph=graph or TeamGraph(),
            created_at=self._now(),
            updated_at=self._now(),
        )
        self._conn.execute(
            "INSERT INTO teams (id, name, graph, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (team.id, team.name, team.graph.model_dump_json(), team.created_at, team.updated_at),
        )
        self._conn.commit()
        return team

    def get(self, team_id: str) -> Team | None:
        row = self._conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        return self._row_to_team(row) if row else None

    def list(self) -> list[Team]:
        rows = self._conn.execute("SELECT * FROM teams ORDER BY created_at").fetchall()
        return [self._row_to_team(r) for r in rows]

    def update_graph(self, team_id: str, graph: TeamGraph) -> Team | None:
        updated_at = self._now()
        cur = self._conn.execute(
            "UPDATE teams SET graph = ?, updated_at = ? WHERE id = ?",
            (graph.model_dump_json(), updated_at, team_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get(team_id)

    def rename(self, team_id: str, name: str) -> Team | None:
        cur = self._conn.execute(
            "UPDATE teams SET name = ?, updated_at = ? WHERE id = ?",
            (name, self._now(), team_id),
        )
        self._conn.commit()
        return self.get(team_id) if cur.rowcount else None

    def delete(self, team_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row_to_team(row: sqlite3.Row) -> Team:
        return Team(
            id=row["id"],
            name=row["name"],
            graph=TeamGraph.model_validate_json(row["graph"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
