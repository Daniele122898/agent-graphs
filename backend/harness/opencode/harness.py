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
import os
from typing import TYPE_CHECKING, Awaitable, Callable

from pydantic_ai import ModelRetry

from ..base import DELEGATION_BUSY_TIMEOUT, MAX_FANOUT, Harness, HistoryView, check_delegation, find_spec
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

# A run must not await session.idle forever: if the server dies or the SSE
# stream drops without a terminal event, the awaiter would hang. Generous (big
# implementation work + a weak local model are slow) but finite — mirrors the
# native finite read timeout. Default 1h; env-tunable. This is the BACKSTOP; the
# first-event watchdog + per-request provider timeouts (config.py) catch the
# common stuck-DeepSeek failures far sooner.
OPENCODE_RUN_TIMEOUT = float(os.environ.get("AGENT_GRAPHS_OPENCODE_RUN_TIMEOUT", "3600"))

# After prompt_async the run must produce SOME event (busy / a message part /
# error) quickly. If nothing arrives, the model never actually ran (bad id/key,
# rate-limited at connect, a no-op) — fail fast with a clear error instead of
# waiting out the whole run budget (the old silent-900s hang). Generous enough
# for a cold first step on the laptop; env-tunable.
OPENCODE_FIRST_EVENT_TIMEOUT = float(os.environ.get("AGENT_GRAPHS_OPENCODE_FIRST_EVENT_TIMEOUT", "120"))

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
        self.started = asyncio.Event()  # set on the first event of a run (watchdog)
        self.busy = False
        self.error: str | None = None
        self.aborting = False  # set by stop() → run_to_completion raises CancelledError
        self.chain: list[str] = []  # delegation chain of the in-flight run (for nested ask_agent)
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
        # signature of the graph the server was last configured with — a change
        # (model/persona/capability/edge edit) triggers a reconfigure.
        self.configured_sig: str = ""
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
        self._ensure_locks: dict[str, asyncio.Lock] = {}

    # --- connection / session lifecycle --------------------------------------

    def token_for(self, session: "Session") -> str:
        """The shared secret the ask_agent callback presents (Phase 5)."""
        return self._tokens.setdefault(session.id, new_id("octok_"))

    @staticmethod
    def _any_busy(rt: _Runtime) -> bool:
        """True if any agent has a run in flight (lock held) or the server
        reports it busy. A reconfigure restarts the server and drops every OC
        session — doing that under a live run (e.g. a delegation chain parked on
        st.idle) orphans the awaiter until the run timeout (the stall)."""
        return any(st.lock.locked() or st.busy for st in rt.agents.values())

    async def _ensure(self, session: "Session") -> _Runtime:
        # Serialize so concurrent first-runs don't spawn two servers and a graph
        # change doesn't reconfigure mid-flight.
        lock = self._ensure_locks.setdefault(session.id, asyncio.Lock())
        async with lock:
            sig = session.graph.model_dump_json()
            rt = self._runtimes.get(session.id)
            if rt is not None and rt.conn.running:
                # A graph/spec edit reconfigures (restarts) the server — but ONLY
                # when nothing is running. The frontend debounce-autosaves the
                # graph (even on a node drag), so reconfiguring mid-run would
                # restart the server under a parked delegation chain and hang
                # every awaiter. Defer the config change to the next idle run
                # (the documented "edit takes effect next run" guarantee).
                if rt.configured_sig != sig and not self._any_busy(rt):
                    await self._reconfigure(session, rt, sig)
                return rt
            conn = self._connect(session, self.token_for(session))
            if asyncio.iscoroutine(conn):
                conn = await conn
            await conn.start()
            rt = _Runtime(conn)
            rt.configured_sig = sig
            rt.listener = asyncio.create_task(self._listen(session, rt))
            self._runtimes[session.id] = rt
            return rt

    async def _reconfigure(self, session: "Session", rt: _Runtime, sig: str) -> None:
        """A graph/spec edit changed the config: restart the server with the new
        config and drop the per-agent OpenCode sessions (they live in the old
        server). The OpenCode-side conversation is lost on restart — the
        edit-takes-effect-next-run guarantee, heavier than native's history
        carry-forward; documented."""
        # Safety net: free any run still parked on st.idle BEFORE tearing the
        # state down. Cancelling the listener (below) skips its stream-death
        # awaiter-freeing path, and rt.agents.clear() drops the _AgentState the
        # awaiters wait on — so without this they hang until OPENCODE_RUN_TIMEOUT.
        # _ensure already refuses to reconfigure while busy; this guarantees
        # fail-fast (RuntimeError -> task parks blocked, retryable) if a
        # reconfigure ever lands mid-run anyway.
        for st in rt.agents.values():
            if not st.idle.is_set():
                st.error = st.error or "[server reconfigured mid-run]"
                st.idle.set()
        if rt.listener is not None:
            rt.listener.cancel()
            try:
                await rt.listener
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await rt.conn.reconfigure(session.graph)
        rt.agents.clear()
        rt.by_oc.clear()
        rt.open_questions.clear()
        rt.configured_sig = sig
        rt.listener = asyncio.create_task(self._listen(session, rt))

    async def _oc_session(self, rt: _Runtime, session: "Session", agent_id: str) -> _AgentState:
        st = rt.agents.setdefault(agent_id, _AgentState())
        if st.oc_session_id is None:
            # Reattach: a persisted OC session id from a previous process still
            # resolves against OpenCode's on-disk store (the re-spawned server has
            # the same repo cwd), so the transcript + conversation survive a
            # restart. Verify it still resolves; otherwise create a fresh session.
            persisted = self._state.get_oc_session(session.id, agent_id)
            if persisted and await self._session_resolves(rt, persisted):
                st.oc_session_id = persisted
            else:
                sid = await rt.conn.client.create_session(agent=agent_id, directory=str(session.repo_root))
                st.oc_session_id = sid
                self._state.set_oc_session(session.id, agent_id, sid)
            rt.by_oc[st.oc_session_id] = agent_id
        return st

    async def _session_resolves(self, rt: _Runtime, oc_session_id: str) -> bool:
        """True if the OC session id still exists on the server (a GET succeeds).
        A persisted id from a prior process may be gone if OpenCode's store was
        cleared — degrade to a fresh session rather than breaking the run."""
        try:
            await rt.conn.client.messages(oc_session_id)
            return True
        except Exception:  # noqa: BLE001
            return False

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
            raise  # shutdown/reconfigure cancelled us — agents torn down separately
        except Exception:  # noqa: BLE001 — stream dropped (server crashed)
            pass
        # Reached only on an UNEXPECTED stream end (clean close or crash), never
        # on cancel (which re-raised above). Free any awaiter parked on st.idle so
        # a run fails fast instead of hanging forever — mirrors session.error.
        for st in rt.agents.values():
            if not st.idle.is_set():
                st.error = st.error or "[opencode event stream closed]"
                st.idle.set()

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
        st.started.set()  # any event for this agent means the run actually started (watchdog)

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
            elif stat == "retry":
                # OpenCode is retrying a transient model error (rate limit / 5xx /
                # "Overloaded"). Its retry loop is UNBOUNDED, so surface it as a
                # visible row — otherwise a rate-limited DeepSeek run just shows
                # "running" forever ("running but doing nothing"). Lifecycle stays
                # running (it may recover); the run budget is the ultimate bound.
                detail = (props.get("status", {}) or {}).get("message") or "transient model error (rate limit / overloaded)"
                bus.publish("retry", {"agent_id": agent_id, "text": f"retrying — {detail}"})
            elif stat == "idle":
                st.busy = False
        elif etype == "message.updated":
            info = props.get("info", {}) or {}
            if info.get("role") == "assistant" and info.get("tokens"):
                st._msg_tokens[info.get("id", "")] = info["tokens"]
                toks = list(st._msg_tokens.values())
                st.usage = {
                    "requests": len(toks),
                    # input is the FULL prompt context per message (cumulative,
                    # grows each turn) — report the latest, don't sum (summing
                    # double-counts the re-sent context). output is per-message;
                    # include reasoning tokens (dropped before) for thinking models.
                    "input_tokens": int(toks[-1].get("input", 0)) if toks else 0,
                    "output_tokens": sum(int(t.get("output", 0)) + int(t.get("reasoning", 0)) for t in toks),
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
            # run complete: compute final text, announce, free awaiters. Skip
            # agent_done when the run errored or was user-aborted (stop already
            # set lifecycle idle in the abort case).
            # Bound the final-output fetch: this single listener processes every
            # agent's events serially, so a slow/hung `messages` GET here would
            # delay idle delivery (and thus run completion) for ALL agents. On
            # timeout/error keep the last streamed output.
            try:
                st.last_output = await asyncio.wait_for(self._final_output(rt, st), timeout=15)
            except Exception:  # noqa: BLE001 — never let final-output fetch stall the stream
                pass
            if st.error is None and not st.aborting:
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
            inp = state.get("input") or {}
            # Emit the call once we ACTUALLY have args (input populated) or the
            # tool has started/finished — NOT on the first "pending" update where
            # input is still {} (that produced the empty "read {}" rows).
            if cid and cid not in st.seen_tool_call and (inp or status in ("running", "completed", "error")):
                st.seen_tool_call.add(cid)
                bus.publish("tool_call", {"agent_id": agent_id, "tool": part.get("tool", ""), "args": inp})
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
        # Acquire the per-agent lock. ``lock_timeout`` bounds LOCK ACQUISITION
        # ONLY (a delegation passes it as a deadlock backstop for a BUSY target →
        # TimeoutError → "busy" ModelRetry); a normal run waits for the lock. The
        # RUN itself is bounded by the run budget below, NOT lock_timeout — those
        # were conflated before, so a delegated run's whole execution was wrongly
        # capped at the (short) busy-timeout.
        if lock_timeout is not None:
            await asyncio.wait_for(st.lock.acquire(), timeout=lock_timeout)
        else:
            await st.lock.acquire()
        try:
            st.idle.clear()
            st.started.clear()
            st.error = None
            st.aborting = False
            st.seen_tool_call.clear()
            st.seen_tool_result.clear()
            # Record this run's delegation chain so a nested ask_agent callback
            # for this agent can read + extend it (cross-hop cycle/depth guard).
            st.chain = list(delegation_chain or [])
            session.bus.publish("user_message", {"agent_id": agent_id, "text": prompt})
            self._lifecycle(session, agent_id, "running")
            await rt.conn.client.prompt_async(
                st.oc_session_id, agent=agent_id, model=self._model_dict(spec.model), text=prompt
            )
            try:
                # Watchdog: the prompt must produce SOME event quickly; if not, the
                # model never ran (bad id/key, rate-limited at connect, no-op) —
                # fail fast instead of burning the whole run budget (the silent
                # 900s hang). idle implies started, so a very fast run is fine.
                try:
                    await asyncio.wait_for(st.started.wait(), timeout=OPENCODE_FIRST_EVENT_TIMEOUT)
                except asyncio.TimeoutError:
                    await rt.conn.client.abort(st.oc_session_id)
                    msg = (
                        f"no response from the model in {int(OPENCODE_FIRST_EVENT_TIMEOUT)}s "
                        f"— check the model id / API key / rate limit for {spec.model}"
                    )
                    session.bus.publish("agent_error", {"agent_id": agent_id, "error": msg})
                    self._lifecycle(session, agent_id, "blocked")
                    raise RuntimeError(msg)
                # Then wait for completion, bounded by the (generous) run budget.
                await asyncio.wait_for(st.idle.wait(), timeout=OPENCODE_RUN_TIMEOUT)
            except asyncio.TimeoutError:
                # the run started but never signalled completion — abort server-side
                # and surface as an error so the agent lands blocked, never hung.
                await rt.conn.client.abort(st.oc_session_id)
                msg = f"run did not complete within {int(OPENCODE_RUN_TIMEOUT)}s"
                session.bus.publish("agent_error", {"agent_id": agent_id, "error": msg})
                self._lifecycle(session, agent_id, "blocked")
                raise RuntimeError(msg)
            finally:
                st.chain = []
            if st.aborting:
                # user pressed Stop mid-run — surface as a cancellation so the
                # TaskRunner parks the task 'blocked' (Retry-able), matching
                # native. Not an error (no agent_error / blocked-with-error).
                # `aborting` is left set; the next run clears it at start, and
                # the listener uses it to suppress a spurious agent_done.
                raise asyncio.CancelledError()
            if st.error:
                raise RuntimeError(st.error)
            return st.last_output
        finally:
            st.lock.release()

    async def submit(self, session: "Session", agent_id: str, prompt: str) -> None:
        # validate up front (404 surfaces to the caller), then dispatch:
        rt = await self._ensure(session)
        if self.is_busy(session, agent_id):
            # INTERJECT: steer the in-flight run instead of queuing behind
            # st.lock (which would block silently — the old "vanished" bug).
            # OpenCode persists the new user message and its run loop re-reads
            # messages each iteration (wrapping a newer user message as a steer
            # reminder), so a second prompt_async on the busy session injects
            # mid-run. We must NOT take st.lock or clear st.idle — the owning
            # run_to_completion still owns the single st.idle await and will see
            # the combined work through to its eventual session.idle.
            st = rt.agents.get(agent_id)
            spec = find_spec(session.graph, agent_id)
            if st is not None and st.oc_session_id is not None and spec is not None:
                session.bus.publish("user_message", {"agent_id": agent_id, "text": prompt})
                try:
                    await rt.conn.client.prompt_async(
                        st.oc_session_id, agent=agent_id, model=self._model_dict(spec.model), text=prompt
                    )
                    return
                except Exception as e:  # noqa: BLE001 — steering failed; surface, don't drop
                    session.bus.publish("agent_error", {"agent_id": agent_id, "error": f"interject failed: {e}"})
                    return
            # raced to idle / state cleared between the busy check and here →
            # fall through to a fresh tracked run rather than dropping the prompt.
        # Not busy: fresh tracked run in the background (HTTP response returns
        # immediately). The per-agent lock inside run_to_completion serializes
        # concurrent fresh submits (native's one-run-at-a-time inbox semantics).
        task = asyncio.create_task(self._submit_bg(session, agent_id, prompt))
        self._bg = getattr(self, "_bg", set())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _submit_bg(self, session: "Session", agent_id: str, prompt: str) -> None:
        try:
            await self.run_to_completion(session, agent_id, prompt)
        except asyncio.CancelledError:
            pass  # user stopped this run (deliberate abort signal, not a task cancel)
        except Exception as e:  # noqa: BLE001
            # run_to_completion already publishes agent_error for in-band failures
            # (timeout, session.error); this catches anything earlier (e.g. the
            # prompt_async POST) so a dropped submit is visible, never swallowed.
            session.bus.publish("agent_error", {"agent_id": agent_id, "error": str(e)})

    # --- non-blocking delegation (opencode) ----------------------------------
    # ask_agent/ask_team on the OpenCode harness DISPATCH: validate the guards
    # synchronously (so a violation still 409s and the model self-corrects), run
    # the target(s) in the BACKGROUND, and INJECT their reply into the asker's
    # session as a follow-up when ready. So the asker's tool call returns
    # immediately and a deep chain never holds nested HTTP fetches + per-agent
    # locks open for the whole subtree (the blocking-delegation fragility that
    # caused the Bun fetch "operation timed out" + orphaned subtrees). The native
    # harness keeps the in-process blocking path (Delegator) — far less fragile.
    #
    # Cross-hop cycle/depth guard still holds: the chain is captured at dispatch
    # and threaded into the target's run (delegation_chain → st.chain), so a
    # target that itself dispatches reads its own in-flight chain via current_chain.

    async def dispatch(self, session: "Session", asker_id: str, target_id: str, question: str, *, chain=None) -> str:
        chain = list(chain or [])
        spec = check_delegation(session.graph, asker_id, target_id, chain)  # raises ModelRetry → 409
        self._record(session, asker_id, spec.id, "question", question)
        self._spawn_delegation(session, asker_id, [(spec.id, question)], chain)
        names = {n.spec.id: n.spec.name for n in session.graph.nodes}
        return (
            f"Delegated to {names.get(spec.id, spec.id)} (`{spec.id}`). Their reply will be delivered "
            "to you as a follow-up message when ready — do NOT wait inline; continue with any "
            "independent work or end your turn, and you'll be re-prompted with their answer."
        )

    async def dispatch_many(self, session: "Session", asker_id: str, requests: list, *, chain=None) -> str:
        chain = list(chain or [])
        if not requests:
            raise ModelRetry("ask_team needs at least one (teammate, task) pair.")
        if len(requests) > MAX_FANOUT:
            raise ModelRetry(f"ask_team is capped at {MAX_FANOUT} teammates at once; split the work across turns.")
        resolved: list[tuple[str, str]] = []
        seen: set[str] = set()
        for ref, q in requests:
            spec = check_delegation(session.graph, asker_id, ref, chain)  # raises ModelRetry → 409
            if spec.id in seen:
                raise ModelRetry(f"'{spec.id}' is listed twice — one teammate does one thing at a time; ask them once.")
            seen.add(spec.id)
            resolved.append((spec.id, q))
        for tid, q in resolved:
            self._record(session, asker_id, tid, "question", q)
        self._spawn_delegation(session, asker_id, resolved, chain)
        names = {n.spec.id: n.spec.name for n in session.graph.nodes}
        who = ", ".join(names.get(t, t) for t, _ in resolved)
        return (
            f"Delegated to {who}. Their replies will be delivered to you together as a follow-up "
            "message when all are done — do NOT wait inline; continue with any independent work or "
            "end your turn, and you'll be re-prompted with their answers."
        )

    def _spawn_delegation(self, session: "Session", asker_id: str, resolved: list, chain: list) -> None:
        task = asyncio.create_task(self._run_delegation(session, asker_id, resolved, chain))
        self._bg = getattr(self, "_bg", set())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _run_delegation(self, session: "Session", asker_id: str, resolved: list, chain: list) -> None:
        """Run delegated target(s) to completion in the background (concurrently),
        then inject their combined reply into the asker's session."""
        child_chain = chain + [asker_id]

        async def one(tid: str, q: str) -> tuple[str, str]:
            try:
                ans = await self.run_to_completion(
                    session, tid, q, delegation_chain=child_chain, lock_timeout=DELEGATION_BUSY_TIMEOUT
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                ans = f"[{tid} was busy too long and didn't reply]"
            except Exception as e:  # noqa: BLE001 — surfaced on the target as agent_error too
                ans = f"[consulting {tid} failed: {e}]"
            self._record(session, tid, asker_id, "reply", ans)
            return tid, ans

        try:
            results = await asyncio.gather(*(one(tid, q) for tid, q in resolved))
        except asyncio.CancelledError:
            return  # session torn down mid-delegation; nothing to inject
        names = {n.spec.id: n.spec.name for n in session.graph.nodes}
        combined = "\n\n".join(f"From {names.get(tid, tid)} (`{tid}`):\n{ans}" for tid, ans in results)
        header = (
            "Reply from the teammate you delegated to:"
            if len(results) == 1
            else "Replies from the teammates you delegated to:"
        )
        try:
            # Deliver into the asker's session: submit() steers a live run or
            # starts a fresh one if the asker already ended its turn.
            await self.submit(session, asker_id, f"{header}\n\n{combined}")
        except Exception as e:  # noqa: BLE001 — never let injection failure crash the bg task
            session.bus.publish("agent_error", {"agent_id": asker_id, "error": f"failed to deliver delegated replies: {e}"})

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
            # mark aborting so an in-flight run_to_completion raises
            # CancelledError (→ task parks blocked, Retry-able) and the listener
            # suppresses a spurious agent_done; then abort + free the awaiter.
            st.aborting = True
            await rt.conn.client.abort(st.oc_session_id)
            self._lifecycle(session, agent_id, "idle")
            st.idle.set()

    def current_chain(self, agent_id: str) -> list[str]:
        """The delegation chain of the agent's in-flight run, so a nested
        ask_agent callback can extend it (cross-hop cycle/depth guard)."""
        for rt in self._runtimes.values():
            st = rt.agents.get(agent_id)
            if st is not None:
                return list(st.chain)
        return []

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
            # Not live in memory (e.g. just after a backend restart). Reattach the
            # transcript ONLY if this agent has a persisted OC session from a prior
            # run — spin up the server to read it. If it never ran, there's nothing
            # to show, so don't spawn a server just to render an empty transcript.
            if not self._state.get_oc_session(session.id, agent_id):
                return HistoryView(instructions=instructions, rows=[], message_count=0)
            rt = await self._ensure(session)
            st = await self._oc_session(rt, session, agent_id)
        try:
            msgs = await rt.conn.client.messages(st.oc_session_id)
        except Exception:  # noqa: BLE001 — a stale/unavailable session must not 500 the tab
            return HistoryView(instructions=instructions, rows=[], message_count=0)
        return HistoryView(instructions=instructions, rows=render_oc_messages(msgs), message_count=len(msgs))

    async def clear_history(self, session: "Session", agent_id: str) -> None:
        rt = self._runtimes.get(session.id)
        st = rt.agents.get(agent_id) if rt else None
        if st and st.oc_session_id:
            await rt.conn.client.delete_session(st.oc_session_id)
            rt.by_oc.pop(st.oc_session_id, None)
            rt.agents[agent_id] = _AgentState()  # fresh session created on next run
            self._state.set_oc_session(session.id, agent_id, "")  # drop the stale reattach pointer

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
        expected = len(rt.open_questions[question_id].get("questions", []))
        if expected and len(answers) != expected:
            raise ValueError(f"expected {expected} answers, got {len(answers)}")
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
