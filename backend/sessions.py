"""Session + SessionManager — the multi-repo spine.

A **Session** is a running instance of a team definition bound to one repo. It
owns — *per instance, never as module globals* — its repo root, filesystem
write-lock, LLM execution gateway, event bus, and agent registry. This is the
single most important structural decision: one uvicorn process holds a
``dict[session_id, Session]``, so N repos run concurrently with zero process
orchestration, and a global lock can never wrongly serialize writes across
unrelated repos.

The **SessionManager** holds that dict and creates sessions from team
definitions, persisting a row in ``sessions`` for each.

Phase 0 establishes the ownership structure with a single auto-created session.
The runtime pieces these own (RunningAgents, real model dispatch) arrive in
Phases 2–6, but the boxes they live in exist now.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Callable

from .bus import EventBus
from .gateway import Gateway
from .models_domain import AgentLifecycle, SessionInfo, SessionMode, TeamGraph
from .stats import UsageTally
from .util import iso_now, new_id


class AgentRegistry:
    """Per-session registry of the team's agents and their lifecycle.

    In Phase 0 it tracks lifecycle strings derived from the team graph. From
    Phase 3 it will hold live ``RunningAgent`` background tasks; the interface
    (track / lifecycle / iterate) is designed to absorb that without callers
    changing.
    """

    def __init__(self) -> None:
        self._lifecycle: dict[str, AgentLifecycle] = {}
        # RunningAgent objects, kept untyped here to avoid a circular import with
        # runtime.py. Populated lazily when an agent is first run.
        self._running: dict[str, object] = {}

    def register(self, agent_id: str, lifecycle: AgentLifecycle = "idle") -> None:
        self._lifecycle[agent_id] = lifecycle

    def attach_running(self, agent_id: str, running: object) -> None:
        self._running[agent_id] = running

    def detach_running(self, agent_id: str) -> None:
        self._running.pop(agent_id, None)

    def running(self, agent_id: str) -> object | None:
        return self._running.get(agent_id)

    def all_running(self) -> list[object]:
        return list(self._running.values())

    def lifecycle(self, agent_id: str) -> AgentLifecycle | None:
        return self._lifecycle.get(agent_id)

    def set_lifecycle(self, agent_id: str, lifecycle: AgentLifecycle) -> None:
        self._lifecycle[agent_id] = lifecycle

    def agent_ids(self) -> list[str]:
        return list(self._lifecycle)

    def all_lifecycles(self) -> dict[str, AgentLifecycle]:
        return dict(self._lifecycle)


class Session:
    """One running instance of a team on a repo. Owns its own everything."""

    def __init__(
        self,
        *,
        session_id: str,
        team_id: str,
        repo_root: Path,
        graph: TeamGraph,
        mode: SessionMode = "parallel",
        status: str = "active",
        created_at: str = "",
    ):
        self.id = session_id
        self.team_id = team_id
        self.repo_root = repo_root
        self.graph = graph
        self.status = status
        self.created_at = created_at

        # Per-session infrastructure — NOT global singletons.
        self.write_lock = asyncio.Lock()
        self.bus = EventBus(session_id)
        # The gateway publishes "waiting for model slot" so the UI can show why
        # an agent is momentarily idle in serial mode.
        self.gateway = Gateway(
            mode=mode,
            on_wait=lambda: self.bus.publish("model_wait", {"session_id": session_id}),
        )
        self.registry = AgentRegistry()
        self.usage = UsageTally()

        # Seed the registry from the team graph so every agent has a lifecycle.
        for node in graph.nodes:
            self.registry.register(node.spec.id, "idle")

    @property
    def mode(self) -> SessionMode:
        return self.gateway.mode

    def info(self) -> SessionInfo:
        return SessionInfo(
            id=self.id,
            team_id=self.team_id,
            repo_path=str(self.repo_root),
            mode=self.mode,
            status=self.status,  # type: ignore[arg-type]
            created_at=self.created_at,
        )


class SessionManager:
    """Holds the live sessions and creates them from team definitions."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], str] = iso_now):
        self._conn = conn
        self._now = clock
        self._sessions: dict[str, Session] = {}

    def create_session(
        self,
        *,
        team_id: str,
        repo_path: str | Path,
        graph: TeamGraph,
        mode: SessionMode = "parallel",
    ) -> Session:
        """Launch a session: persist a row and build the live ``Session``.

        The graph is passed in (the caller pins the team definition at launch —
        editing the template later must not mutate a running session).
        """
        session_id = new_id("sess_")
        created_at = self._now()
        repo_root = Path(repo_path).resolve()
        self._conn.execute(
            "INSERT INTO sessions (id, team_id, repo_path, mode, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, team_id, str(repo_root), mode, "active", created_at),
        )
        self._conn.commit()
        session = Session(
            session_id=session_id,
            team_id=team_id,
            repo_root=repo_root,
            graph=graph,
            mode=mode,
            created_at=created_at,
        )
        self._sessions[session_id] = session
        return session

    def resume_session(self, session_id: str, graph: TeamGraph) -> Session | None:
        """Rehydrate a persisted session into memory (pause repo today, resume
        tomorrow). Reconstructs the live ``Session`` from its DB row + the team
        graph; per-agent conversation histories are reloaded lazily when each
        ``RunningAgent`` is (re)created. Returns None if no such session row.
        """
        if session_id in self._sessions:
            return self._sessions[session_id]
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        session = Session(
            session_id=row["id"],
            team_id=row["team_id"],
            repo_root=Path(row["repo_path"]),
            graph=graph,
            mode=row["mode"],
            status=row["status"],
            created_at=row["created_at"],
        )
        self._sessions[session_id] = session
        return session

    def latest_session_id_for_team(self, team_id: str) -> str | None:
        """The most recent persisted session bound to a team (for reuse on
        startup so edits persist across restarts instead of accumulating)."""
        row = self._conn.execute(
            "SELECT id FROM sessions WHERE team_id = ? ORDER BY created_at DESC LIMIT 1",
            (team_id,),
        ).fetchone()
        return row["id"] if row else None

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list(self) -> list[Session]:
        return list(self._sessions.values())

    def active_sessions_for_repo(self, repo_path: str | Path) -> list[Session]:
        """Sessions currently bound to a repo — used for the same-repo warning
        (Phase 8). Two task forces fighting over one repo is allowed but flagged.
        """
        target = str(Path(repo_path).resolve())
        return [s for s in self._sessions.values() if str(s.repo_root) == target]
