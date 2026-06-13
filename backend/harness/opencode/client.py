"""A thin async client over the OpenCode HTTP API + SSE event stream.

Just the subset the harness uses, shaped to the live 1.16.2 contract (see
specs/opencode-transition.md Part A/B). This is the seam tests mock: the harness
talks only to an ``OpenCodeClient``-shaped object, so a fake (no server, no LLM)
drops in for deterministic end-to-end tests.

Gotchas baked in here:
- ``prompt_async`` returns 204/empty — completion is observed via the SSE
  ``session.idle`` event, not this response.
- create-session ``model`` uses key ``id``; prompt ``model`` uses ``modelID``.
- SSE frames are ``data: {id,type,properties}``; ``sessionID`` is inside
  ``properties``.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx


class OpenCodeClient:
    """Wraps a started ``httpx.AsyncClient`` pointed at an OpenCode server."""

    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def create_session(self, *, agent: str, directory: str, title: str = "") -> str:
        r = await self._http.post(
            "/session", params={"directory": directory}, json={"title": title or agent, "agent": agent}
        )
        r.raise_for_status()
        return r.json()["id"]

    async def prompt_async(self, session_id: str, *, agent: str, model: dict, text: str) -> None:
        """Fire a prompt; returns immediately (204). Drive completion off SSE."""
        r = await self._http.post(
            f"/session/{session_id}/prompt_async",
            json={"agent": agent, "model": model, "parts": [{"type": "text", "text": text}]},
        )
        if r.status_code not in (200, 202, 204):
            r.raise_for_status()

    async def messages(self, session_id: str) -> list[dict]:
        r = await self._http.get(f"/session/{session_id}/message")
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, list) else body.get("data", [])

    async def abort(self, session_id: str) -> None:
        try:
            await self._http.post(f"/session/{session_id}/abort")
        except httpx.HTTPError:
            pass

    async def summarize(self, session_id: str) -> None:
        r = await self._http.post(f"/session/{session_id}/summarize")
        r.raise_for_status()

    async def delete_session(self, session_id: str) -> None:
        try:
            await self._http.delete(f"/session/{session_id}")
        except httpx.HTTPError:
            pass

    async def agent_info(self, name: str) -> dict | None:
        r = await self._http.get("/agent")
        if r.status_code != 200:
            return None
        for a in r.json():
            if a.get("name") == name:
                return a
        return None

    async def list_questions(self) -> list[dict]:
        r = await self._http.get("/question")
        if r.status_code != 200:
            return []
        body = r.json()
        return body if isinstance(body, list) else body.get("data", [])

    async def reply_question(self, request_id: str, answers: list[list[str]]) -> bool:
        r = await self._http.post(f"/question/{request_id}/reply", json={"answers": answers})
        return r.status_code == 200

    async def events(self) -> AsyncIterator[dict]:
        """Yield decoded SSE events until the stream closes (server shutdown)."""
        async with self._http.stream("GET", "/event", timeout=None) as resp:
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue
