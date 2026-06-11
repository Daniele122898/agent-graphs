"""Continuous persistence of per-agent runtime state.

From Phase 3 onward, an agent's conversation history + lifecycle + usage are
written to the ``agent_state`` table as they change, keyed by
``(session_id, agent_id)``. This is the "whole context and state" that enables
snapshot/resume — the *resume-and-rehydrate* logic + UI is the only part
deferred to Phase 9; the *writing* happens now.

Pydantic AI messages serialize via ``ModelMessagesTypeAdapter`` (stable JSON),
so a stored history can be handed straight back as ``message_history`` later.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from ..util import iso_now


class AgentStateStore:
    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], str] = iso_now):
        self._conn = conn
        self._now = clock

    def save(
        self,
        session_id: str,
        agent_id: str,
        *,
        messages: list[ModelMessage] | None = None,
        lifecycle: str = "idle",
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Upsert the agent's state. Serializes messages to JSON."""
        history_json = ModelMessagesTypeAdapter.dump_json(messages or []).decode("utf-8")
        usage_json = json.dumps(usage or {})
        self._conn.execute(
            """
            INSERT INTO agent_state (session_id, agent_id, history, lifecycle, usage, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (session_id, agent_id) DO UPDATE SET
                history = excluded.history,
                lifecycle = excluded.lifecycle,
                usage = excluded.usage,
                updated_at = excluded.updated_at
            """,
            (session_id, agent_id, history_json, lifecycle, usage_json, self._now()),
        )
        self._conn.commit()

    def set_lifecycle(self, session_id: str, agent_id: str, lifecycle: str) -> None:
        """Cheap lifecycle-only update (doesn't touch history)."""
        self._conn.execute(
            """
            INSERT INTO agent_state (session_id, agent_id, lifecycle, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (session_id, agent_id) DO UPDATE SET
                lifecycle = excluded.lifecycle, updated_at = excluded.updated_at
            """,
            (session_id, agent_id, lifecycle, self._now()),
        )
        self._conn.commit()

    def load_messages(self, session_id: str, agent_id: str) -> list[ModelMessage]:
        row = self._conn.execute(
            "SELECT history FROM agent_state WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        ).fetchone()
        if row is None or not row["history"]:
            return []
        return list(ModelMessagesTypeAdapter.validate_json(row["history"]))

    def get(self, session_id: str, agent_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM agent_state WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        ).fetchone()
        return dict(row) if row else None
