"""A deterministic, in-process fake of the OpenCode server — no subprocess, no
LLM. It implements the slice of the client surface the harness uses and drives
scripted "turns" (parts emitted as SSE events ending in session.idle), so the
OpenCodeHarness is exercised end-to-end like the native FunctionModel tests.

A turn is a list of OpenCode parts, or ``{"parts": [...], "todos": [...]}`` to
also emit a ``todo.updated``. Parts are raw OpenCode part dicts, e.g.::

    {"type": "text", "text": "done", "time": {"start": 0, "end": 1}}
    {"type": "tool", "tool": "write", "callID": "c1",
     "state": {"status": "completed", "input": {...}, "output": "ok"}}
"""

from __future__ import annotations

import asyncio
from collections import defaultdict


def text_part(text: str) -> dict:
    return {"type": "text", "text": text, "time": {"start": 0, "end": 1}}


def tool_part(tool: str, call_id: str, inp: dict, output: str = "ok") -> dict:
    return {
        "type": "tool", "tool": tool, "callID": call_id,
        "state": {"status": "completed", "input": inp, "output": output, "time": {"start": 0, "end": 1}},
    }


class FakeOpenCodeClient:
    def __init__(self, script: dict[str, list]):
        # script: agent_name -> list of turns (one per prompt to that agent)
        self._script = script
        self._events: asyncio.Queue = asyncio.Queue()
        self._messages: dict[str, list] = defaultdict(list)
        self._agent_of: dict[str, str] = {}
        self._turn: dict[str, int] = defaultdict(int)
        self._questions: list[dict] = []
        self._pending: dict[str, str] = {}  # qid -> session_id parked on it
        self.replied: list[tuple[str, list]] = []
        self._n = 0
        self._closed = False

    async def create_session(self, *, agent: str, directory: str, title: str = "") -> str:
        self._n += 1
        sid = f"ses_fake{self._n}"
        self._agent_of[sid] = agent
        return sid

    async def prompt_async(self, session_id: str, *, agent: str, model: dict, text: str) -> None:
        self._messages[session_id].append({"info": {"role": "user"}, "parts": [text_part(text)]})
        turns = self._script.get(agent, [])
        idx = self._turn[session_id]
        self._turn[session_id] += 1
        turn = turns[min(idx, len(turns) - 1)] if turns else [text_part("ok")]

        # A park turn goes busy and never idles (simulates a slow/hung run):
        # for stop() and listener-death tests.
        if isinstance(turn, dict) and turn.get("park"):
            await self._events.put({"type": "session.status", "properties": {"sessionID": session_id, "status": {"type": "busy"}}})
            return

        # A silent turn emits NOTHING — models a model that never responds (bad
        # id/key, no-op). The first-event watchdog must fire.
        if isinstance(turn, dict) and turn.get("silent"):
            return

        # A retry turn surfaces OpenCode's transient-retry status, then completes —
        # for asserting the retry row is published (not a mute "running").
        if isinstance(turn, dict) and "retry" in turn:
            await self._events.put({"type": "session.status", "properties": {"sessionID": session_id, "status": {"type": "busy"}}})
            await self._events.put({"type": "session.status", "properties": {"sessionID": session_id, "status": {"type": "retry", "message": turn["retry"]}}})
            assistant = {"info": {"role": "assistant", "id": f"msg_{session_id}_r", "tokens": {"input": 5, "output": 3}},
                         "parts": [text_part("recovered after retry")]}
            self._messages[session_id].append(assistant)
            await self._events.put({"type": "message.part.updated", "properties": {"sessionID": session_id, "part": assistant["parts"][0]}})
            await self._events.put({"type": "message.updated", "properties": {"sessionID": session_id, "info": assistant["info"]}})
            await self._events.put({"type": "session.status", "properties": {"sessionID": session_id, "status": {"type": "idle"}}})
            await self._events.put({"type": "session.idle", "properties": {"sessionID": session_id}})
            return

        # An error turn goes busy then emits session.error (+ idle).
        if isinstance(turn, dict) and "error" in turn:
            await self._events.put({"type": "session.status", "properties": {"sessionID": session_id, "status": {"type": "busy"}}})
            await self._events.put({"type": "session.error", "properties": {"sessionID": session_id, "error": {"message": turn["error"]}}})
            await self._events.put({"type": "session.idle", "properties": {"sessionID": session_id}})
            return

        # A question turn parks: emit question.asked and DO NOT go idle until
        # reply_question() is called (which then continues + goes idle).
        if isinstance(turn, dict) and "question" in turn:
            await self._events.put({"type": "session.status", "properties": {"sessionID": session_id, "status": {"type": "busy"}}})
            self._n += 1
            qid = f"que_fake{self._n}"
            self._questions.append({"id": qid, "sessionID": session_id, "questions": [turn["question"]]})
            self._pending[qid] = session_id
            await self._events.put({"type": "question.asked", "properties": {"id": qid, "sessionID": session_id, "questions": [turn["question"]]}})
            return

        parts = turn["parts"] if isinstance(turn, dict) else turn
        todos = turn.get("todos") if isinstance(turn, dict) else None

        await self._events.put({"type": "session.status", "properties": {"sessionID": session_id, "status": {"type": "busy"}}})
        assistant = {"info": {"role": "assistant", "id": f"msg_{session_id}_{idx}", "tokens": {"input": 10, "output": 5}}, "parts": []}
        for part in parts:
            assistant["parts"].append(part)
            await self._events.put({"type": "message.part.updated", "properties": {"sessionID": session_id, "part": part}})
        self._messages[session_id].append(assistant)
        await self._events.put({"type": "message.updated", "properties": {"sessionID": session_id, "info": assistant["info"]}})
        if todos is not None:
            await self._events.put({"type": "todo.updated", "properties": {"sessionID": session_id, "todos": todos}})
        await self._events.put({"type": "session.status", "properties": {"sessionID": session_id, "status": {"type": "idle"}}})
        await self._events.put({"type": "session.idle", "properties": {"sessionID": session_id}})

    async def messages(self, session_id: str) -> list:
        return list(self._messages[session_id])

    async def abort(self, session_id: str) -> None:
        await self._events.put({"type": "session.status", "properties": {"sessionID": session_id, "status": {"type": "idle"}}})

    async def summarize(self, session_id: str) -> None:
        self._messages[session_id] = [{"info": {"role": "user"}, "parts": [text_part("[summary]")]}]

    async def delete_session(self, session_id: str) -> None:
        self._messages.pop(session_id, None)

    async def agent_info(self, name: str) -> dict | None:
        return {"name": name, "prompt": "fake prompt"}

    def queue_question(self, q: dict) -> None:
        self._questions.append(q)

    async def list_questions(self) -> list:
        return list(self._questions)

    async def reply_question(self, request_id: str, answers: list) -> bool:
        self.replied.append((request_id, answers))
        self._questions = [q for q in self._questions if q.get("id") != request_id]
        sid = self._pending.pop(request_id, None)
        if sid is None:
            return True
        # the run resumes: announce the reply, then complete with a final text
        chosen = answers[0][0] if answers and answers[0] else ""
        await self._events.put({"type": "question.replied", "properties": {"id": request_id, "sessionID": sid}})
        assistant = {"info": {"role": "assistant", "id": f"msg_{sid}_q", "tokens": {"input": 5, "output": 3}},
                     "parts": [text_part(f"proceeding with {chosen}")]}
        self._messages[sid].append(assistant)
        await self._events.put({"type": "message.part.updated", "properties": {"sessionID": sid, "part": assistant["parts"][0]}})
        await self._events.put({"type": "message.updated", "properties": {"sessionID": sid, "info": assistant["info"]}})
        await self._events.put({"type": "session.status", "properties": {"sessionID": sid, "status": {"type": "idle"}}})
        await self._events.put({"type": "session.idle", "properties": {"sessionID": sid}})
        return True

    async def events(self):
        while not self._closed:
            ev = await self._events.get()
            if ev is None:
                break
            yield ev

    def close(self) -> None:
        self._closed = True
        self._events.put_nowait(None)


class FakeConnection:
    def __init__(self, client: FakeOpenCodeClient):
        self.client = client
        self.running = True
        self.reconfigured = 0

    async def start(self) -> None:
        self.running = True

    async def reconfigure(self, graph) -> None:
        self.reconfigured += 1

    async def aclose(self) -> None:
        self.running = False
        self.client.close()


def fake_connect(client: FakeOpenCodeClient):
    """A ``connect`` factory the OpenCodeHarness accepts: every session shares
    this one fake client."""
    def _connect(session, token):
        return FakeConnection(client)

    return _connect
