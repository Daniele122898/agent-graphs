"""Endpoint-contract backstops: make the "new mutating route forgot the guard"
class fail LOUDLY rather than ship silently (how the rebind endpoint first shipped
sync + unguarded). The prose contract lives in backend/api/CLAUDE.md; these tests
enforce it by route-table introspection — no DB/lifespan needed."""

from __future__ import annotations

import inspect

from backend.main import create_app


def _routes(app):
    for r in app.routes:
        yield r, (getattr(r, "methods", set()) or set()), getattr(r, "path", "")


def test_session_graph_mutators_are_async(tmp_path):
    # Swapping session.graph off the event loop (a sync `def` handler runs in a
    # threadpool) races the harness's mid-run reads of it — these MUST be async.
    app = create_app(db_path=tmp_path / "t.sqlite")
    must_be_async = {
        ("POST", "/api/sessions/{session_id}/rebind"),
        ("PUT", "/api/teams/{team_id}/graph"),
    }
    found = set()
    for r, methods, path in _routes(app):
        for m in methods:
            if (m, path) in must_be_async:
                found.add((m, path))
                assert inspect.iscoroutinefunction(r.endpoint), \
                    f"{m} {path} swaps session.graph and MUST be `async def` (event-loop serialization)"
    assert found == must_be_async, f"graph-mutator route(s) missing/renamed: {must_be_async - found}"


# Every POST/PUT under /api/session(s) or /api/agent is consciously classified —
# GUARDED (mutates state a live run depends on → must busy-guard via
# wiring.require_*_idle) or EXEMPT. A new such route in NEITHER set fails the
# test below, forcing the guard decision at authoring time.
_GUARDED = {
    "/api/sessions/{session_id}/rebind",
    "/api/agent/{agent_id}/history/clear",
    "/api/agent/{agent_id}/history/summarize",
}
_EXEMPT = {
    "/api/sessions",                      # launch — brand-new session, nothing running
    "/api/sessions/{session_id}/resume",  # rehydrate a persisted session
    "/api/session/mode",                  # gateway mode toggle (not history/graph)
    "/api/agent/{agent_id}/run",          # the run itself
    "/api/agent/{agent_id}/interject",    # designed to inject INTO a live run
    "/api/agent/{agent_id}/stop",         # stop is FOR busy agents
}


def test_mutating_session_agent_routes_are_classified(tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite")
    classified = _GUARDED | _EXEMPT
    for _r, methods, path in _routes(app):
        if not ({"POST", "PUT"} & methods):
            continue
        if path.startswith("/api/session") or path.startswith("/api/agent"):
            assert path in classified, (
                f"unclassified mutating-shaped route {path!r}: decide whether it mutates "
                "state a live run depends on. If yes, busy-guard it (wiring.require_*_idle) "
                "and add it to _GUARDED; if no, add it to _EXEMPT. See backend/api/CLAUDE.md."
            )


# Team-library mutating routes carry their OWN guard story (not the busy-guard):
# the graph PUT swaps session.graph (must be async — covered above); DELETE must
# refuse while a session is bound (block-if-bound, not require_*_idle); create +
# metadata touch only the teams table. Each is consciously classified so a new
# team route can't silently skip the deletion-safety / async decision.
_TEAM_ROUTES = {
    ("POST", "/api/teams"),                  # create — brand-new team, nothing bound
    ("PATCH", "/api/teams/{team_id}"),       # name/description — not read by any live run
    ("DELETE", "/api/teams/{team_id}"),      # block-if-bound (sessions_using_team → 409)
    ("PUT", "/api/teams/{team_id}/graph"),   # session-syncing — async (see must_be_async)
}


def test_mutating_team_routes_are_classified(tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite")
    for _r, methods, path in _routes(app):
        if not ({"POST", "PUT", "PATCH", "DELETE"} & methods):
            continue
        if not path.startswith("/api/teams"):
            continue
        for m in methods & {"POST", "PUT", "PATCH", "DELETE"}:
            assert (m, path) in _TEAM_ROUTES, (
                f"unclassified team-mutating route {m} {path!r}: if it swaps "
                "session.graph make it async; if it removes a team, guard it "
                "block-if-bound (SessionManager.sessions_using_team). See "
                "backend/api/CLAUDE.md, then add it to _TEAM_ROUTES."
            )
