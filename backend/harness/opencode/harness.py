"""OpenCodeHarness — runs a session's agents on a headless OpenCode server and
translates its HTTP/SSE surface into the ``Harness`` interface + our event bus.

One OpenCode server per session (lazy), one OpenCode session per team agent
(lazy), and one background listener per server that demuxes the global SSE
stream by ``properties.sessionID`` → our agent id and republishes onto
``session.bus`` with our event names, plus drives the lifecycle ``registry`` and
records per-agent usage. ``session.idle`` is the run-complete signal: the
listener fetches the final assistant text, publishes ``agent_done``, and frees
any awaiter. Live tool progress streams from ``message.part.updated``.

The ``connect`` seam (default spawns a real ``OpenCodeServer``) is what tests
override with a fake — no server, no LLM.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Awaitable, Callable

from ..base import Harness, HistoryView, find_spec
from ...providers.registry import split_model_string
from ...runtime.tasks import ReviewVerdict
from ...util import new_id
from .client import OpenCodeClient
from .prompt import build_opencode_prompt
from .render import render_oc_messages
from .server import OpenCodeServer

if TYPE_CHECKING:
    from ...agents.a2a import MessageLog
    from ...runtime.sessions import Session
    from ...storage.agent_state import AgentStateStore

CONTINUATION_NUDGES = 2

REVIEW_PROMPT = (
    "You are reviewing whether a task result fully satisfies the task. Reply with "
    "ONLY a JSON object: {{\"approved\": true|false, \"critique\": \"...\"}} — approve "
    "only if the result fully satisfies the task; otherwise reject with a concrete, "
    "actionable critique.\n\nTask:\n{task}\n\nResult to review:\n{result}"
)


class Connection:
    """Wraps an ``OpenCodeServer`` as the client the harness consumes. The
    default; tests inject a fake with the same surface."""

    def __init__(self, server: OpenCodeServer):
        self._server = server
        self.client: OpenCodeClient | None = None

    @property
    def running(self) -> bool:
        return self._server.running

    async def start(self) -> None:
        await self._server.start()
        self.client = OpenCodeClient(self._server.client())

    async def reconfigure(self, graph) -> None:
        await self._server.reconfigure(graph)
        self.client = OpenCodeClient(self._server.client())

    async def aclose(self) -> None:
        await self._server.shutdown()


def _default_connect(session: "Session", token: str) -> Connection:
    return Connection(
        OpenCodeServer(
            session_id=session.id,
            repo_root=session.repo_root,
            graph=session.graph,
            callback_token=token,
        )
    )


class _AgentState:
    def __init__(self) -> None:
        self.oc_session_id: str | None = None
        self.lock = asyncio.Lock()
        self.idle = asyncio.Event()
        self.busy = False
        self.error: str | None = None
        self.last_output = ""
        self.todos: list[dict] = []
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        self._msg_tokens: dict[str, dict] = {}
        self.seen_tool_call: set[str] = set()
        self.seen_tool_result: set[str] = set()


class _Runtime:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.agents: dict[str, _AgentState] = {}
        self.by_oc: dict[str, str] = {}
        self.listener: asyncio.Task | None = None
        # Open ask_user questions, cached from question.asked SSE events
        # (keyed by OpenCode question id) so the sync list endpoint can read
        # them without an HTTP round-trip.
        self.open_questions: dict[str, dict] = {}


class OpenCodeHarness(Harness):
    id = "opencode"

    def __init__(
        self,
        *,
        state_store: "AgentStateStore",
        message_log: "MessageLog",
        repo_root=None,
        connect: Callable[["Session", str], Connection | Awaitable[Connection]] | None = None,
    ):
        self._state = state_store
        self.message_log = message_log
        self._connect = connect or _default_connect
        self._runtimes: dict[str, _Runtime] = {}
        self._tokens: dict[str, str] = {}

    # --- connection / session lifecycle --------------------------------------

    def token_for(self, session: "Session") -> str:
        """The shared secret the ask_agent callback presents (Phase 5)."""
        return self._tokens.setdefault(session.id, new_id("octok_"))

    async def _ensure(self, session: "Session") -> _Runtime:
        rt = self._runtimes.get(session.id)
        if rt is not None and rt.conn.running:
            return rt
        conn = self._connect(session, self.token_for(session))
        if asyncio.iscoroutine(conn):
            conn = await conn
        await conn.start()
        rt = _Runtime(conn)
        rt.listener = asyncio.create_task(self._listen(session, rt))
        self._runtimes[session.id] = rt
        return rt

    async def _oc_session(self, rt: _Runtime, session: "Session", agent_id: str) -> _AgentState:
        st = rt.agents.setdefault(agent_id, _AgentState())
        if st.oc_session_id is None:
            sid = await rt.conn.client.create_session(agent=agent_id, directory=str(session.repo_root))
            st.oc_session_id = sid
            rt.by_oc[sid] = agent_id
        return st

    async def start(self, session: "Session") -> None:
        await self._ensure(session)

    async def shutdown(self, session: "Session") -> None:
        rt = self._runtimes.pop(session.id, None)
        if rt is None:
            return
        if rt.listener is not None:
            rt.listener.cancel()
            try:
                await rt.listener
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await rt.conn.aclose()

    # --- event listener / translation ----------------------------------------

    async def _listen(self, session: "Session", rt: _Runtime) -> None:
        try:
            async for ev in rt.conn.client.events():
                try:
                    await self._handle_event(session, rt, ev)
                except Exception:  # noqa: BLE001 — never let one event kill the stream
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — stream closed (server shutdown)
            pass

    def _lifecycle(self, session: "Session", agent_id: str, lifecycle: str) -> None:
        session.registry.set_lifecycle(agent_id, lifecycle)  # type: ignore[arg-type]
        session.bus.publish("agent_lifecycle", {"agent_id": agent_id, "lifecycle": lifecycle})

    def _oc_session_id_of(self, props: dict) -> str | None:
        return (
            props.get("sessionID")
            or (props.get("part", {}) or {}).get("sessionID")
            or (props.get("info", {}) or {}).get("sessionID")
        )

    async def _handle_event(self, session: "Session", rt: _Runtime, ev: dict) -> None:
        etype = ev.get("type", "")
        props = ev.get("properties", {}) or {}
        ocsid = self._oc_session_id_of(props)
        agent_id = rt.by_oc.get(ocsid) if ocsid else None
        if agent_id is None:
            return  # server-level event or not one of our agent sessions
        st = rt.agents[agent_id]
        bus = session.bus

        if etype == "message.part.updated":
            self._emit_part(bus, st, agent_id, props.get("part", {}) or {})
        elif etype == "todo.updated":
            st.todos = props.get("todos", []) or []
            bus.publish("todos", {"agent_id": agent_id, "todos": st.todos})
        elif etype == "session.status":
            stat = (props.get("status", {}) or {}).get("type")
            if stat == "busy":
                st.busy = True
                self._lifecycle(session, agent_id, "running")
            elif stat == "idle":
                st.busy = False
        elif etype == "message.updated":
            info = props.get("info", {}) or {}
            if info.get("role") == "assistant" and info.get("tokens"):
                st._msg_tokens[info.get("id", "")] = info["tokens"]
                st.usage = {
                    "requests": len(st._msg_tokens),
                    "input_tokens": sum(int(t.get("input", 0)) for t in st._msg_tokens.values()),
                    "output_tokens": sum(int(t.get("output", 0)) for t in st._msg_tokens.values()),
                }
        elif etype == "question.asked":
            self._on_question_asked(session, rt, agent_id, props)
        elif etype in ("question.replied", "question.rejected"):
            qid = props.get("id") or props.get("requestID")
            rt.open_questions.pop(qid, None)
            session.bus.publish("user_question_done", {"id": qid, "agent_id": agent_id})
            self._lifecycle(session, agent_id, "running")
        elif etype == "session.error":
            st.error = json.dumps(props.get("error", props))[:500]
            bus.publish("agent_error", {"agent_id": agent_id, "error": st.error})
            self._lifecycle(session, agent_id, "blocked")
            st.idle.set()
        elif etype == "session.idle":
            # run complete: compute final text, announce, free awaiters.
            st.last_output = await self._final_output(rt, st)
            if st.error is None:
                bus.publish("agent_done", {"agent_id": agent_id, "output": st.last_output})
                self._lifecycle(session, agent_id, "idle")
            st.idle.set()

    def _on_question_asked(self, session: "Session", rt: _Runtime, agent_id: str, props: dict) -> None:
        qid = props.get("id") or props.get("requestID")
        if not qid:
            return
        questions = [
            {"question": q.get("question", ""), "options": [o.get("label", "") for o in (q.get("options") or [])]}
            for q in (props.get("questions") or [])
        ]
        payload = {"id": qid, "agent_id": agent_id, "questions": questions, "created_at": ""}
        rt.open_questions[qid] = payload
        session.bus.publish("user_question", payload)
        self._lifecycle(session, agent_id, "waiting-on-user")

    def _emit_part(self, bus, st: _AgentState, agent_id: str, part: dict) -> None:
        ptype = part.get("type")
        ended = (part.get("time", {}) or {}).get("end") is not None
        if ptype == "text" and ended and part.get("text"):
            bus.publish("text", {"agent_id": agent_id, "text": part["text"]})
        elif ptype == "reasoning" and ended and part.get("text"):
            bus.publish("thinking", {"agent_id": agent_id, "text": part["text"]})
        elif ptype == "tool":
            cid = part.get("callID") or part.get("id") or ""
            state = part.get("state", {}) or {}
            status = state.get("status")
            if cid and cid not in st.seen_tool_call:
                st.seen_tool_call.add(cid)
                bus.publish("tool_call", {"agent_id": agent_id, "tool": part.get("tool", ""), "args": state.get("input", {}) or {}})
            if status in ("completed", "error") and cid and cid not in st.seen_tool_result:
                st.seen_tool_result.add(cid)
                out = state.get("output") if status == "completed" else state.get("error", "error")
                bus.publish("tool_result", {"agent_id": agent_id, "tool": part.get("tool", ""), "result": str(out)})

    async def _final_output(self, rt: _Runtime, st: _AgentState) -> str:
        """The text of the most recent assistant message — the run's output."""
        if st.oc_session_id is None:
            return ""
        try:
            msgs = await rt.conn.client.messages(st.oc_session_id)
        except Exception:  # noqa: BLE001
            return st.last_output
        for m in reversed(msgs):
            info = m.get("info", m)
            if info.get("role") == "assistant":
                texts = [p.get("text", "") for p in m.get("parts", []) if p.get("type") == "text" and p.get("text")]
                if texts:
                    return "\n".join(texts).strip()
        return st.last_output

    # --- execution -----------------------------------------------------------

    def _model_dict(self, model_str: str) -> dict:
        backend, name = split_model_string(model_str)
        return {"providerID": backend, "modelID": name}

    async def run_to_completion(
        self, session: "Session", agent_id: str, prompt: str, *, usage=None, delegation_chain=None, lock_timeout=None
    ) -> str:
        spec = find_spec(session.graph, agent_id)
        if spec is None:
            from fastapi import HTTPException

            raise HTTPException(404, f"no agent '{agent_id}' in this session")
        rt = await self._ensure(session)
        st = await self._oc_session(rt, session, agent_id)
        async with st.lock:
            st.idle.clear()
            st.error = None
            st.seen_tool_call.clear()
            st.seen_tool_result.clear()
            session.bus.publish("user_message", {"agent_id": agent_id, "text": prompt})
            self._lifecycle(session, agent_id, "running")
            await rt.conn.client.prompt_async(
                st.oc_session_id, agent=agent_id, model=self._model_dict(spec.model), text=prompt
            )
            if lock_timeout:
                await asyncio.wait_for(st.idle.wait(), timeout=lock_timeout)
            else:
                await st.idle.wait()
            if st.error:
                raise RuntimeError(st.error)
            return st.last_output

    async def submit(self, session: "Session", agent_id: str, prompt: str) -> None:
        # validate up front (404 surfaces to the caller), then run in the
        # background so the HTTP response returns immediately. The per-agent
        # lock inside run_to_completion serializes concurrent submits (native's
        # one-run-at-a-time inbox semantics).
        await self._ensure(session)
        task = asyncio.create_task(self._submit_bg(session, agent_id, prompt))
        self._bg = getattr(self, "_bg", set())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _submit_bg(self, session: "Session", agent_id: str, prompt: str) -> None:
        try:
            await self.run_to_completion(session, agent_id, prompt)
        except Exception:  # noqa: BLE001 — already surfaced via agent_error on the bus
            pass

    async def run_for_task(self, session: "Session", agent_id: str, prompt: str) -> str:
        output = await self.run_to_completion(session, agent_id, prompt)
        rt = self._runtimes.get(session.id)
        for _ in range(CONTINUATION_NUDGES):
            st = rt.agents.get(agent_id) if rt else None
            open_items = [t for t in (st.todos if st else []) if t.get("status") != "completed"]
            if not open_items:
                break
            bullet = "\n".join(f"- [{t.get('status')}] {t.get('content')}" for t in open_items)
            output = await self.run_to_completion(
                session,
                agent_id,
                "Your run ended but your todo list still has open items:\n"
                f"{bullet}\n\nContinue working through them now. If an item is genuinely "
                "done, mark it completed. If you need the user, use the question tool. If "
                "something blocks you, state exactly what.",
            )
        return output

    async def run_reviewer(self, session: "Session", reviewer_id: str, task_prompt: str, result: str) -> ReviewVerdict:
        spec = find_spec(session.graph, reviewer_id)
        if spec is None:
            return ReviewVerdict(approved=True, critique=f"(no reviewer '{reviewer_id}'; auto-approved)")
        text = await self.run_to_completion(
            session, reviewer_id, REVIEW_PROMPT.format(task=task_prompt, result=result)
        )
        return _parse_verdict(text)

    async def stop(self, session: "Session", agent_id: str) -> None:
        rt = self._runtimes.get(session.id)
        if rt is None:
            return
        st = rt.agents.get(agent_id)
        if st and st.oc_session_id:
            await rt.conn.client.abort(st.oc_session_id)
            self._lifecycle(session, agent_id, "idle")
            st.error = None
            st.idle.set()

    def is_busy(self, session: "Session", agent_id: str) -> bool:
        rt = self._runtimes.get(session.id)
        st = rt.agents.get(agent_id) if rt else None
        return bool(st and (st.busy or st.lock.locked()))

    # --- history / context ---------------------------------------------------

    async def history(self, session: "Session", agent_id: str) -> HistoryView:
        spec = find_spec(session.graph, agent_id)
        if spec is None:
            from fastapi import HTTPException

            raise HTTPException(404, f"no agent '{agent_id}' in this session")
        instructions = [build_opencode_prompt(spec, session.graph, session.repo_root)]
        rt = self._runtimes.get(session.id)
        st = rt.agents.get(agent_id) if rt else None
        if st is None or st.oc_session_id is None:
            return HistoryView(instructions=instructions, rows=[], message_count=0)
        msgs = await rt.conn.client.messages(st.oc_session_id)
        return HistoryView(instructions=instructions, rows=render_oc_messages(msgs), message_count=len(msgs))

    async def clear_history(self, session: "Session", agent_id: str) -> None:
        rt = self._runtimes.get(session.id)
        st = rt.agents.get(agent_id) if rt else None
        if st and st.oc_session_id:
            await rt.conn.client.delete_session(st.oc_session_id)
            rt.by_oc.pop(st.oc_session_id, None)
            rt.agents[agent_id] = _AgentState()  # fresh session created on next run

    async def summarize_history(self, session: "Session", agent_id: str) -> list[dict]:
        rt = self._runtimes.get(session.id)
        st = rt.agents.get(agent_id) if rt else None
        if st is None or st.oc_session_id is None:
            from fastapi import HTTPException

            raise HTTPException(409, "no history to summarize")
        await rt.conn.client.summarize(st.oc_session_id)
        msgs = await rt.conn.client.messages(st.oc_session_id)
        return render_oc_messages(msgs)

    # --- questions / usage ---------------------------------------------------

    def list_questions(self, session: "Session") -> list[dict]:
        rt = self._runtimes.get(session.id)
        if rt is None:
            return []
        return list(rt.open_questions.values())

    async def answer_question(self, session: "Session", question_id: str, answers: list[str]) -> bool:
        rt = self._runtimes.get(session.id)
        if rt is None or question_id not in rt.open_questions:
            return False
        # OpenCode wants one answer-array per question; our UI sends one string
        # per question, so wrap each as a single selection.
        ok = await rt.conn.client.reply_question(question_id, [[a] for a in answers])
        if ok:
            rt.open_questions.pop(question_id, None)
        return ok

    def usage(self, session: "Session", agent_id: str) -> dict:
        rt = self._runtimes.get(session.id)
        st = rt.agents.get(agent_id) if rt else None
        return dict(st.usage) if st else {"requests": 0, "input_tokens": 0, "output_tokens": 0}


def _parse_verdict(text: str) -> ReviewVerdict:
    """Parse a reviewer's JSON reply. Conservative: unparseable → not approved
    (forces a revision rather than a false pass; the revision cap then blocks)."""
    import re

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return ReviewVerdict(approved=bool(data.get("approved")), critique=str(data.get("critique", "")))
        except (json.JSONDecodeError, ValueError):
            pass
    return ReviewVerdict(approved=False, critique=f"(could not parse reviewer output) {text[:300]}")
