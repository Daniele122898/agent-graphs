"""Schema tests: the four-table spine exists and carries the keyed columns.

These assert the load-bearing design decision (everything keyed by
team_id/session_id), not arbitrary constants — if a keyed column went missing,
multi-repo support would silently break, which is exactly a "broken" failure.
"""

from __future__ import annotations

from backend import db as db_module


def test_all_four_tables_exist(conn):
    assert {"teams", "sessions", "agent_state", "tasks"} <= db_module.table_names(conn)


def test_init_db_is_idempotent(conn):
    # Calling again must not raise or duplicate.
    db_module.init_db(conn)
    db_module.init_db(conn)
    assert {"teams", "sessions", "agent_state", "tasks"} <= db_module.table_names(conn)


def test_sessions_keyed_by_team_and_repo(conn):
    cols = db_module.column_names(conn, "sessions")
    assert {"id", "team_id", "repo_path", "mode", "status"} <= cols


def test_agent_state_keyed_by_session_and_agent(conn):
    cols = db_module.column_names(conn, "agent_state")
    assert {"session_id", "agent_id", "history", "lifecycle"} <= cols


def test_tasks_keyed_by_session_with_delegation_tree(conn):
    cols = db_module.column_names(conn, "tasks")
    assert {
        "session_id",
        "assigned_agent_id",
        "status",
        "completion_signal",
        "parent_task_id",
        "delegation_chain",
    } <= cols


def test_foreign_keys_enforced(conn):
    # PRAGMA foreign_keys is ON, so an orphan session insert should fail.
    import sqlite3

    try:
        conn.execute(
            "INSERT INTO sessions (id, team_id, repo_path) VALUES (?, ?, ?)",
            ("s1", "no_such_team", "/tmp/x"),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
