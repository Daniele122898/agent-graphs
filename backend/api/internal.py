"""Internal callbacks the OpenCode harness's custom tools POST back into.

Localhost-only, authenticated by a per-session token the harness injects into
the OpenCode server's env (so a stray request can't drive delegation). Today:
the ``ask_agent`` bridge — the OpenCode-side tool can't enforce our graph guards
or drive the target's persistent session, so it hands the call here, where
``Harness.delegate`` applies the shared neighbor/cycle/depth guards and runs the
target. Returns the teammate's answer as plain text (the tool returns it to the
model); a guard violation comes back as 409 with the corrective message, which
the tool surfaces to the model as a tool error to self-correct on.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic_ai import ModelRetry

from .schemas import AskAgentInternalRequest, AskTeamInternalRequest


def install(app: FastAPI) -> None:
    def _authed_session(session_id: str, token: str):
        """Resolve + token-authenticate a callback. 404 unknown, 403 bad token."""
        session = app.state.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "no such session")
        token_for = getattr(session.harness, "token_for", None)
        if token_for is None or token != token_for(session):
            raise HTTPException(403, "invalid or missing callback token")
        return session

    def _chain_for(session, asker_id: str):
        # Thread the asker's in-flight delegation chain so the cross-hop
        # cycle/depth guards accumulate across HTTP callbacks (A→B→C…).
        current = getattr(session.harness, "current_chain", None)
        return current(asker_id) if current else None

    @app.post("/internal/ask_agent", response_class=PlainTextResponse)
    async def internal_ask_agent(body: AskAgentInternalRequest, x_ag_token: str = Header(default="")) -> str:
        session = _authed_session(body.session_id, x_ag_token)
        chain = _chain_for(session, body.asker_id)
        # NON-BLOCKING on opencode (dispatch): validate synchronously, run the
        # target in the background, inject its reply into the asker when ready —
        # so the asker's tool fetch returns immediately (deep chains don't pin
        # nested fetches/locks). Fall back to blocking delegate for any harness
        # without dispatch (native never reaches /internal — its ask_agent is
        # in-process).
        run = getattr(session.harness, "dispatch", None) or session.harness.delegate
        try:
            return await run(session, body.asker_id, body.target_id, body.question, chain=chain)
        except ModelRetry as e:
            # guard violation → corrective message (surfaced as a tool error on
            # the OpenCode side so the model self-corrects).
            raise HTTPException(409, str(e))

    @app.post("/internal/ask_team", response_class=PlainTextResponse)
    async def internal_ask_team(body: AskTeamInternalRequest, x_ag_token: str = Header(default="")) -> str:
        """Parallel-delegation callback: fan work out to several teammates at once
        (non-blocking dispatch_many — background runs, replies injected together)."""
        session = _authed_session(body.session_id, x_ag_token)
        chain = _chain_for(session, body.asker_id)
        pairs = [(a.target_id, a.task) for a in body.assignments]
        run = getattr(session.harness, "dispatch_many", None) or session.harness.delegate_many
        try:
            return await run(session, body.asker_id, pairs, chain=chain)
        except ModelRetry as e:
            raise HTTPException(409, str(e))
