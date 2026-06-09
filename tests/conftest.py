"""Shared test fixtures.

The guiding rule (per the spec): test *behavior*, never constants; inject the
clock and use temp dirs so tests are deterministic and isolated.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend import db as db_module


@pytest.fixture
def fake_clock():
    """A monotonically-incrementing fake clock returning ISO-ish strings, so
    created_at/updated_at are deterministic and ordered without wall-clock."""
    counter = {"n": 0}

    def clock() -> str:
        counter["n"] += 1
        return f"2026-01-01T00:00:{counter['n']:02d}+00:00"

    return clock


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """A fresh, schema-initialized SQLite connection on a temp file."""
    c = db_module.connect(tmp_path / "test.sqlite")
    db_module.init_db(c)
    yield c
    c.close()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A temp directory standing in for a session's repo."""
    r = tmp_path / "repo"
    r.mkdir()
    return r
