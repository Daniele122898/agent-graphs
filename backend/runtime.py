"""RunningAgent — an agent as a long-lived background worker.

This is the change from a naive chat app: an agent is not a request/response
handler but a durable ``asyncio`` task with a lifecycle
(idle/running/waiting-on-agent/blocked/done). It owns an **inbox** of prompts;
the UI *observes and interjects*, it does not drive turn-by-turn.

Conversation history is preserved across prompts (via ``message_history``), so a
follow-up or an interjection builds on prior context, and it is persisted to
``agent_state`` continuously (snapshot-for-resume; rehydration UI is Phase 9).

**Interjection semantics (an honest design choice):** Pydantic AI runs a whole
multi-turn ``iter`` to completion; you cannot splice a user message between its
internal model turns. So an interjection submitted while the agent is running is
*queued* and processed the moment the current run finishes, with full history —
which reads as continuous work. To truly interrupt, ``stop()`` cancels the
in-flight run. This keeps behavior robust rather than fighting the framework.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Callable

from pydantic_ai.models import Model

from .a2a import Delegator, MessageLog
from .agents import build_agent
from .gateway import GatedModel
from .models import resolve_model
from .models_domain import AgentLifecycle, AgentSpec
from .streaming import run_agent_streamed
from .todos import AgentDeps
from .tools import DevTools

if TYPE_CHECKING:  # avoid a circular import with sessions.py
    from .agent_state import AgentStateStore
    from .sessions import Session

_STOP = object()  # sentinel pushed to the inbox to end the loop cleanly


class RunningAgent:
    def __init__(
        self,
        *,
        session: "Session",
        spec: AgentSpec,
        model: Model,
        state_store: "AgentStateStore | None" = None,
        message_log: "MessageLog | None" = None,
        model_resolver: Callable[[str], Model] = resolve_model,
        bash_runner: Callable | None = None,
        initial_messages: list | None = None,
    ):
        self.session = session
        self.spec = spec
        # A deep snapshot of the spec this worker was built from, so a later
        # edit (model/persona/capabilities) is detected even if the graph node
        # is mutated in place — the caller rebuilds the worker on the next run.
        self._built_spec = spec.model_copy(deep=True)
        self.agent_id = spec.id
        self._state_store = state_store
        self._message_log = message_log
        self._inbox: asyncio.Queue = asyncio.Queue()
        # Seed from persisted history on resume, so the agent continues with
        # full prior context rather than a blank slate.
        self._messages: list = list(initial_messages or [])
        self._task: asyncio.Task | None = None
        # The in-flight run_once (task/delegation path) — tracked so stop()
        # can cancel it; the inbox-loop path is covered by cancelling _task.
        self._current_run: asyncio.Future | None = None
        self._stopped = False
        self._bash_runner = bash_runner
        self._model_resolver = model_resolver
        # One run at a time per worker: an agent is one "person". Concurrent
        # work (a second task, a delegated question) queues here rather than
        # interleaving into the same conversation history.
        self._run_lock = asyncio.Lock()

        # Route this agent's model calls through the session gateway (serial on
        # low-spec machines, pass-through otherwise).
        self.agent = build_agent(spec, model=GatedModel(model, session.gateway), dev_tools=self._dev_tools(spec))

        # A delegator lets this agent (and its delegation targets) consult
        # neighbors via ask_agent. Targets are real RunningAgents obtained from
        # the session registry (created on demand), so delegated work is fully
        # visible: lifecycle, streamed events, and persisted history.
        delegator = Delegator(session, self._obtain_target, message_log=message_log)
        self._deps = AgentDeps(
            session_id=session.id,
            agent_id=spec.id,
            delegator=delegator,
            question_board=session.questions,
        )

    def _dev_tools(self, spec: AgentSpec) -> DevTools:
        kwargs = {"write_lock": self.session.write_lock}
        if self._bash_runner is not None:
            kwargs["bash_runner"] = self._bash_runner
        return DevTools(self.session.repo_root, spec.capabilities, **kwargs)

    async def _obtain_target(self, spec: AgentSpec) -> "RunningAgent":
        """Delegation targets get (or become) real registered workers, built
        with the same injected services as this one."""
        return await obtain_worker(
            self.session,
            spec,
            state_store=self._state_store,
            message_log=self._message_log,
            model_resolver=self._model_resolver,
            bash_runner=self._bash_runner,
        )

    # lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    def submit(self, prompt: str) -> None:
        """Queue a prompt. If idle it runs now; if running it's an interjection
        processed right after the current run (with history)."""
        self.start()  # lazy: workers created for delegation have no loop yet
        self._inbox.put_nowait(prompt)

    async def run_once(
        self,
        prompt: str,
        *,
        usage=None,
        usage_limits=None,
        delegation_chain: list[str] | None = None,
        lock_timeout: float | None = None,
    ) -> str:
        """Run a single prompt to completion and return the output (awaitable).

        Used by the task system (which needs the result back to apply a
        completion gate) and by delegation (``ask_agent`` routes here so the
        target's work is visible and persisted). Shares the agent's history and
        delegator, so continuity behaves identically to the inbox path.

        ``usage``/``usage_limits`` thread a parent run's budget through a
        delegated run; ``delegation_chain`` carries the cycle/depth guard state.
        ``lock_timeout`` bounds the wait for this worker to become free (the
        delegation path uses it as a deadlock backstop); ``None`` waits forever.
        """
        if lock_timeout is None:
            await self._run_lock.acquire()
        else:
            await asyncio.wait_for(self._run_lock.acquire(), timeout=lock_timeout)
        deps = self._deps if delegation_chain is None else replace(self._deps, delegation_chain=list(delegation_chain))
        history_out: list = []
        try:
            self._current_run = asyncio.ensure_future(
                run_agent_streamed(
                    bus=self.session.bus,
                    registry=self.session.registry,
                    agent_id=self.agent_id,
                    agent=self.agent,
                    prompt=prompt,
                    deps=deps,
                    usage_tally=self.session.usage,
                    message_history=self._messages or None,
                    history_out=history_out,
                    usage=usage,
                    usage_limits=usage_limits,
                )
            )
            return await self._current_run
        finally:
            self._current_run = None
            self._run_lock.release()
            # Adopt the run's messages in finally so a FAILED run's partial
            # transcript (filled by run_agent_streamed's error path) is kept
            # and persisted too — not just successful runs.
            if history_out:
                self._messages = history_out
            self._persist()
            if self._inbox.empty():
                self._set_lifecycle("idle")

    async def stop(self) -> None:
        """Cancel any in-flight run — both the inbox-loop path and a run_once
        driven by a task or delegation — and end the loop. Idempotent."""
        self._stopped = True
        for fut in (self._current_run, self._task):
            if fut is not None:
                fut.cancel()
                try:
                    await fut
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001 — already surfaced as agent_error
                    pass
        self._current_run = None
        self._task = None
        self._set_lifecycle("idle")

    @property
    def messages(self) -> list:
        return self._messages

    @property
    def busy(self) -> bool:
        """True while a run is in flight (inbox-loop or run_once path)."""
        return self._run_lock.locked()

    @property
    def todos(self) -> list:
        """The agent's current checklist (shared AgentDeps survive across runs)."""
        return list(self._deps.todos)

    def replace_history(self, messages: list) -> None:
        """Swap the conversation wholesale (clear / summarize-compact). The
        caller must ensure no run is in flight — see ``busy``."""
        self._messages = list(messages)
        self._persist()

    def spec_changed(self, current_spec) -> bool:
        """True if the agent's config changed since this worker was built — the
        signal to rebuild it so a model/persona/capability edit takes effect."""
        return self._built_spec != current_spec

    # internals ---------------------------------------------------------

    def _set_lifecycle(self, lifecycle: AgentLifecycle) -> None:
        self.session.registry.set_lifecycle(self.agent_id, lifecycle)
        self.session.bus.publish("agent_lifecycle", {"agent_id": self.agent_id, "lifecycle": lifecycle})
        if self._state_store is not None:
            self._state_store.set_lifecycle(self.session.id, self.agent_id, lifecycle)

    async def _loop(self) -> None:
        while not self._stopped:
            prompt = await self._inbox.get()
            if prompt is _STOP:
                break
            history_out: list = []
            try:
                async with self._run_lock:
                    await run_agent_streamed(
                        bus=self.session.bus,
                        registry=self.session.registry,
                        agent_id=self.agent_id,
                        agent=self.agent,
                        prompt=prompt,
                        deps=self._deps,
                        usage_tally=self.session.usage,
                        message_history=self._messages or None,
                        history_out=history_out,
                    )
            except asyncio.CancelledError:
                raise  # stop() requested — leave the loop without marking blocked
            except Exception:  # noqa: BLE001 — already surfaced as agent_error
                pass
            finally:
                # in finally so a failed run's partial transcript survives too
                if history_out:
                    self._messages = history_out
                self._persist()
            # Ready for the next task only if nothing is queued (queued work
            # keeps the agent visibly "running").
            if self._inbox.empty():
                self._set_lifecycle("idle")

    def _persist(self) -> None:
        if self._state_store is not None:
            self._state_store.save(
                self.session.id,
                self.agent_id,
                messages=self._messages,
                lifecycle=self.session.registry.lifecycle(self.agent_id) or "idle",
                usage=self.session.usage.get(self.agent_id),
            )


async def obtain_worker(
    session: "Session",
    spec: AgentSpec,
    *,
    state_store: "AgentStateStore | None" = None,
    message_log: "MessageLog | None" = None,
    model_resolver: Callable[[str], Model] = resolve_model,
    bash_runner: Callable | None = None,
) -> RunningAgent:
    """Get the registered worker for ``spec`` — or (re)build and register one.

    The single get-or-create path shared by the HTTP layer and delegation. If a
    live worker exists but its spec changed (model/persona/capability edit), it
    is stopped and rebuilt carrying its conversation history forward; otherwise
    history is seeded from the persisted state. The inbox loop starts lazily on
    the first ``submit``, so workers created purely for ``run_once`` (tasks,
    delegation) don't hold an idle background task.
    """
    existing = session.registry.running(spec.id)
    if existing is not None:
        if not existing.spec_changed(spec):
            return existing
        prior_messages = list(existing.messages)
        await existing.stop()
        session.registry.detach_running(spec.id)
    else:
        prior_messages = state_store.load_messages(session.id, spec.id) if state_store else []

    ra = RunningAgent(
        session=session,
        spec=spec,
        model=model_resolver(spec.model),
        state_store=state_store,
        message_log=message_log,
        model_resolver=model_resolver,
        bash_runner=bash_runner,
        initial_messages=prior_messages,
    )
    session.registry.attach_running(spec.id, ra)
    return ra
