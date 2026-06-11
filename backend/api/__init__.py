"""The HTTP/SSE surface, grouped by resource.

Each module exposes ``install(app)`` and registers its endpoints as closures
over the app (the same style ``create_app`` always used — handlers reach state
via ``app.state``, never module globals, so tests can run many isolated apps).
``main.create_app`` owns the lifespan/boot; the non-trivial glue behind the
endpoints lives in ``wiring.py``; request bodies in ``schemas.py``.
"""

from __future__ import annotations

from fastapi import FastAPI

from . import agents, questions, sessions, stats, tasks, teams


def install_routes(app: FastAPI) -> None:
    """Register every endpoint group on the app."""
    for module in (sessions, teams, agents, questions, stats, tasks):
        module.install(app)
