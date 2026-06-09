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
from typing import TYPE_CHECKING, Callable

from pydantic_ai.models import Model

from .a2a import Delegator, MessageLog
from .agents import build_agent
from .gateway import GatedModel
from .models import resolve_model
from .models_domain import AgentSpec
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
        self.agent_id = spec.id
        self._state_store = state_store
        self._inbox: asyncio.Queue = asyncio.Queue()
        # Seed from persisted history on resume, so the agent continues with
        # full prior context rather than a blank slate.
        self._messages: list = list(initial_messages or [])
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._bash_runner = bash_runner
        self._model_resolver = model_resolver

        # Route this agent's model calls through the session gateway (serial on
        # low-spec machines, pass-through otherwise).
        self.agent = build_agent(spec, model=GatedModel(model, session.gateway), dev_tools=self._dev_tools(spec))

        # A delegator lets this agent (and its delegation targets) consult
        # neighbors via ask_agent. Targets are built on demand with their own
        # model + capabilities (resolved via the injected resolver).
        delegator = Delegator(session, self._build_target, message_log=message_log)
        self._deps = AgentDeps(session_id=session.id, agent_id=spec.id, delegator=delegator)

    def _dev_tools(self, spec: AgentSpec) -> DevTools:
        kwargs = {"write_lock": self.session.write_lock}
        if self._bash_runner is not None:
            kwargs["bash_runner"] = self._bash_runner
        return DevTools(self.session.repo_root, spec.capabilities, **kwargs)

    def _build_target(self, spec: AgentSpec):
        model = GatedModel(self._model_resolver(spec.model), self.session.gateway)
        return build_agent(spec, model=model, dev_tools=self._dev_tools(spec))

    # lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    def submit(self, prompt: str) -> None:
        """Queue a prompt. If idle it runs now; if running it's an interjection
        processed right after the current run (with history)."""
        self._inbox.put_nowait(prompt)

    async def run_once(self, prompt: str) -> str:
        """Run a single prompt to completion and return the output (awaitable).

        Used by the task system, which needs the result back to apply a
        completion gate — distinct from the interactive ``submit`` inbox path.
        Shares the agent's history/delegator, so delegated work and continuity
        behave identically.
        """
        history_out: list = []
        try:
            output = await run_agent_streamed(
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
            if history_out:
                self._messages = history_out
            return output
        finally:
            self._persist()
            if self._inbox.empty():
                self._set_lifecycle("idle")

    async def stop(self) -> None:
        """Cancel any in-flight run and end the loop. Idempotent."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._set_lifecycle("idle")

    @property
    def messages(self) -> list:
        return self._messages

    # internals ---------------------------------------------------------

    def _set_lifecycle(self, lifecycle: str) -> None:
        self.session.registry.set_lifecycle(self.agent_id, lifecycle)  # type: ignore[arg-type]
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
                if history_out:
                    self._messages = history_out
            except asyncio.CancelledError:
                raise  # stop() requested — leave the loop without marking blocked
            except Exception:  # noqa: BLE001 — already surfaced as agent_error
                pass
            finally:
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
