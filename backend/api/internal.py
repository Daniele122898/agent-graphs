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

from .schemas import AskAgentInternalRequest


def install(app: FastAPI) -> None:
    @app.post("/internal/ask_agent", response_class=PlainTextResponse)
    async def internal_ask_agent(body: AskAgentInternalRequest, x_ag_token: str = Header(default="")) -> str:
        session = app.state.sessions.get(body.session_id)
        if session is None:
            raise HTTPException(404, "no such session")
        token_for = getattr(session.harness, "token_for", None)
        if token_for is None or x_ag_token != token_for(session):
            raise HTTPException(403, "invalid or missing callback token")
        try:
            return await session.harness.delegate(
                session, body.asker_id, body.target_id, body.question
            )
        except ModelRetry as e:
            # guard violation / busy / consult failure → corrective message the
            # model can act on (surfaced as a tool error on the OpenCode side).
            raise HTTPException(409, str(e))
