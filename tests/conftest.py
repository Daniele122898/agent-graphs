"""Shared test fixtures.

The guiding rule (per the spec): test *behavior*, never constants; inject the
clock and use temp dirs so tests are deterministic and isolated.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, ModelResponsePart
from pydantic_ai.models.function import FunctionModel

from backend import db as db_module


def make_sequence_model(turns: list[list[ModelResponsePart]]) -> FunctionModel:
    """A FunctionModel that emits a scripted sequence of responses, one per
    model call. ``turns[i]`` is the list of parts for the i-th assistant turn;
    the last turn repeats if the model is called again. This is the deterministic,
    zero-token seam that drives whole agent/session runs in tests.
    """

    def fn(messages, info):
        idx = sum(1 for m in messages if isinstance(m, ModelResponse))
        parts = turns[min(idx, len(turns) - 1)]
        return ModelResponse(parts=parts)

    return FunctionModel(fn)


def bootstrap_session(client, repo_path, *, graph=None, mode="parallel", name="T"):
    """Explicit team+session setup for API tests (the app no longer auto-creates
    anything). Returns (team_dict, session_dict)."""
    body = {"name": name}
    if graph is not None:
        body["graph"] = graph
    team = client.post("/api/teams", json=body).json()
    session = client.post(
        "/api/sessions",
        json={"team_id": team["id"], "repo_path": str(repo_path), "mode": mode},
    ).json()
    return team, session


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
