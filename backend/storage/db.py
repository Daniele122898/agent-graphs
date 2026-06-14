"""SQLite persistence: connection management + the four-table schema.

The four tables are the spine described in the spec:

- ``teams``       — reusable, repo-agnostic team definitions (templates).
- ``sessions``    — running instances binding a ``team_id`` to a ``repo_path``.
- ``agent_state`` — per ``(session_id, agent_id)`` conversation/lifecycle state
                    (written continuously from Phase 3; resume logic in Phase 9).
- ``tasks``       — per ``session_id`` work items + their status lifecycle.

Every row is keyed by ``team_id`` / ``session_id`` from day one so multi-repo /
multi-session support is a matter of UI later, not a schema rewrite.

This module owns *schema and connections only*. Table-specific CRUD lives with
the component that owns the table (e.g. ``teams.py`` owns ``teams``), so the
logic stays close to the abstraction it serves.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Default on-disk location — backend/db.sqlite (one level above this package),
# so the user's existing database survives source reorganizations. Tests pass
# ":memory:" or a temp path instead.
DEFAULT_DB_PATH = Path(__file__).parent.parent / "db.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    graph       TEXT NOT NULL DEFAULT '{}',   -- JSON: TeamGraph
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    team_id     TEXT NOT NULL,
    repo_path   TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'parallel',  -- parallel | serial
    status      TEXT NOT NULL DEFAULT 'active',    -- active | paused | stopped
    created_at  TEXT NOT NULL DEFAULT '',
    harness     TEXT NOT NULL DEFAULT 'native',    -- native | opencode
    FOREIGN KEY (team_id) REFERENCES teams (id)
);

CREATE TABLE IF NOT EXISTS agent_state (
    session_id          TEXT NOT NULL,
    agent_id            TEXT NOT NULL,
    history             TEXT NOT NULL DEFAULT '[]',  -- JSON: serialized messages
    compacted_context   TEXT NOT NULL DEFAULT '',
    lifecycle           TEXT NOT NULL DEFAULT 'idle',
    usage               TEXT NOT NULL DEFAULT '{}',  -- JSON: token usage
    oc_session_id       TEXT NOT NULL DEFAULT '',     -- opencode harness: its OC session id (reattach across restart)
    updated_at          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session_id, agent_id),
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    title               TEXT NOT NULL,
    prompt              TEXT NOT NULL,
    assigned_agent_id   TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'queued',
    completion_signal   TEXT NOT NULL DEFAULT 'self_reported',
    todos               TEXT NOT NULL DEFAULT '[]',  -- JSON
    parent_task_id      TEXT,
    delegation_chain    TEXT NOT NULL DEFAULT '[]',  -- JSON
    result              TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT '',
    updated_at          TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- 'question' | 'reply'
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_team   ON sessions (team_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_session   ON tasks (session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent    ON tasks (parent_task_id);
CREATE INDEX IF NOT EXISTS idx_agentstate_sess ON agent_state (session_id);
"""


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with sane defaults for this app.

    ``check_same_thread=False`` because the async event loop may touch the
    connection from worker threads; we serialize writes at a higher layer.
    ``Row`` factory gives dict-like access. Foreign keys are enforced.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply the schema idempotently. Safe to call on every startup."""
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, non-destructive column migrations for existing databases.

    ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a new
    column added to the schema above must also be back-filled here for the
    user's pre-existing ``db.sqlite``. Each step is guarded by a column check.
    """
    if "harness" not in column_names(conn, "sessions"):
        conn.execute("ALTER TABLE sessions ADD COLUMN harness TEXT NOT NULL DEFAULT 'native'")
    if "oc_session_id" not in column_names(conn, "agent_state"):
        conn.execute("ALTER TABLE agent_state ADD COLUMN oc_session_id TEXT NOT NULL DEFAULT ''")


def table_names(conn: sqlite3.Connection) -> set[str]:
    """The set of user tables present — used by tests and health checks."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r["name"] for r in rows}


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names for a table — used by tests to assert the keyed columns."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}
