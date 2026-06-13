"""NativeHarness — the pydantic-ai harness behind the ``Harness`` interface.

A thin wrapper over the existing machinery: ``obtain_worker``/``RunningAgent``
for execution, the session's own ``QuestionBoard``/``UsageTally``/``Gateway``/
``registry`` for the rest. Behavior is identical to the pre-abstraction code —
this only relocates call sites behind the interface.

Model resolution defers to ``wiring.resolve_model`` *at call time* (not a
captured reference), so the long-standing test seam
(``monkeypatch.setattr(wiring, "resolve_model", ...)``) keeps working unchanged.

The harness is stateless per session (all per-session state lives on the
``Session``: registry of live workers, question board, usage, gateway), so one
instance is shared by every native session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import UsageLimits

from ..agents.history import render_messages
from ..agents.persona import build_instructions, environment_instructions
from ..agents.a2a import neighbor_instructions
from ..runtime.gateway import GatedModel
from ..runtime.tasks import ReviewVerdict
from ..runtime.workers import obtain_worker
from .base import Harness, HistoryView, find_spec

if TYPE_CHECKING:
    from ..agents.a2a import MessageLog
    from ..domain.models import AgentSpec
    from ..runtime.sessions import Session
    from ..storage.agent_state import AgentStateStore

CONTINUATION_NUDGES = 2
"""How many times a task run that stops with unfinished todos gets re-prompted.
Small local models drift into ending their turn mid-plan; the nudge converts
"accidentally stopped" into "kept working" without risking an infinite loop."""

REVIEW_GUIDANCE = (
    "\n\nYou are acting as a reviewer. Decide whether the result fully satisfies "
    "the task. Approve only if it does; otherwise reject with a concrete, "
    "actionable critique of what is missing or wrong."
)

SUMMARIZE_PROMPT = (
    "Summarize this entire conversation into a compact briefing for yourself: "
    "what was asked, what you did (files created or changed, key decisions, "
    "results), and any open follow-ups. Write it so you could continue the "
    "work from the summary alone. Reply with ONLY the summary."
)


class NativeHarness(Harness):
    id = "native"

    def __init__(
        self,
        *,
        state_store: "AgentStateStore",
        message_log: "MessageLog",
        bash_runner=None,
    ):
        self._state = state_store
        self.message_log = message_log
        self._bash = bash_runner

    def _resolve(self, model_str: str):
        # Looked up on the wiring module at call time so the test monkeypatch
        # (`wiring.resolve_model`) takes effect; never captured at import.
        from .. import wiring

        return wiring.resolve_model(model_str)

    def _spec_or_raise(self, session: "Session", agent_id: str) -> "AgentSpec":
        spec = find_spec(session.graph, agent_id)
        if spec is None:
            from fastapi import HTTPException

            raise HTTPException(404, f"no agent '{agent_id}' in this session")
        return spec

    async def _worker(self, session: "Session", agent_id: str):
        spec = self._spec_or_raise(session, agent_id)
        return await obtain_worker(
            session,
            spec,
            state_store=self._state,
            message_log=self.message_log,
            model_resolver=self._resolve,
            bash_runner=self._bash,
        )

    # --- bring-up / teardown -------------------------------------------------

    async def shutdown(self, session: "Session") -> None:
        for ra in session.registry.all_running():
            await ra.stop()

    # --- execution -----------------------------------------------------------

    async def submit(self, session: "Session", agent_id: str, prompt: str) -> None:
        ra = await self._worker(session, agent_id)
        ra.submit(prompt)

    async def run_to_completion(
        self,
        session: "Session",
        agent_id: str,
        prompt: str,
        *,
        usage=None,
        delegation_chain: list[str] | None = None,
        lock_timeout: float | None = None,
    ) -> str:
        ra = await self._worker(session, agent_id)
        # When run as a delegation (a chain is threaded), bound the request budget
        # exactly as the in-process Delegator does.
        usage_limits = UsageLimits(request_limit=50) if delegation_chain is not None else None
        return await ra.run_once(
            prompt,
            usage=usage,
            usage_limits=usage_limits,
            delegation_chain=delegation_chain,
            lock_timeout=lock_timeout,
        )

    async def run_for_task(self, session: "Session", agent_id: str, prompt: str) -> str:
        ra = await self._worker(session, agent_id)
        output = await ra.run_once(prompt)
        # Anti-stall: a run that ends while its own checklist has open items
        # either forgot to finish or forgot to update the list — both deserve a
        # nudge rather than silently calling the task complete.
        for _ in range(CONTINUATION_NUDGES):
            open_items = [t for t in ra.todos if t.status != "completed"]
            if not open_items:
                break
            bullet = "\n".join(f"- [{t.status}] {t.content}" for t in open_items)
            output = await ra.run_once(
                "Your run ended but your todo list still has open items:\n"
                f"{bullet}\n\n"
                "Continue working through them now. If an item is genuinely done, "
                "mark it completed via write_todos. If you need the user, call "
                "ask_user. If something blocks you, state exactly what."
            )
        return output

    async def run_reviewer(
        self, session: "Session", reviewer_id: str, task_prompt: str, result: str
    ) -> ReviewVerdict:
        spec = find_spec(session.graph, reviewer_id)
        if spec is None:
            return ReviewVerdict(approved=True, critique=f"(no reviewer '{reviewer_id}'; auto-approved)")
        from ..providers.registry import thinking_settings

        reviewer = Agent(
            model=GatedModel(self._resolve(spec.model), session.gateway),
            output_type=ReviewVerdict,
            instructions=(spec.persona or f"You are {spec.name}.") + REVIEW_GUIDANCE,
            model_settings=thinking_settings(spec.model, spec.thinking, spec.thinking_effort),
        )
        r = await reviewer.run(f"Task:\n{task_prompt}\n\nResult to review:\n{result}")
        return r.output

    async def stop(self, session: "Session", agent_id: str) -> None:
        ra = session.registry.running(agent_id)
        if ra is not None:
            await ra.stop()
            session.registry.detach_running(agent_id)  # allow a fresh start

    def is_busy(self, session: "Session", agent_id: str) -> bool:
        ra = session.registry.running(agent_id)
        return ra is not None and ra.busy

    # --- history / context ---------------------------------------------------

    def _messages(self, session: "Session", agent_id: str) -> list[ModelMessage]:
        ra = session.registry.running(agent_id)
        if ra is not None:
            return list(ra.messages)
        return self._state.load_messages(session.id, agent_id)

    def _context_sections(self, session: "Session", spec: "AgentSpec") -> list[str]:
        sections = [
            build_instructions(spec),
            neighbor_instructions(session.graph, spec.id),
            environment_instructions(spec, session.repo_root),
        ]
        return [s for s in sections if s]

    def _set_messages(self, session: "Session", agent_id: str, messages: list[ModelMessage]) -> None:
        ra = session.registry.running(agent_id)
        if ra is not None:
            ra.replace_history(messages)  # persists via the worker
        else:
            self._state.save(
                session.id,
                agent_id,
                messages=messages,
                lifecycle=session.registry.lifecycle(agent_id) or "idle",
                usage=session.usage.get(agent_id),
            )

    async def history(self, session: "Session", agent_id: str) -> HistoryView:
        spec = self._spec_or_raise(session, agent_id)
        msgs = self._messages(session, agent_id)
        return HistoryView(
            instructions=self._context_sections(session, spec),
            rows=render_messages(msgs),
            message_count=len(msgs),
        )

    async def clear_history(self, session: "Session", agent_id: str) -> None:
        self._set_messages(session, agent_id, [])

    async def summarize_history(self, session: "Session", agent_id: str) -> list[dict]:
        spec = self._spec_or_raise(session, agent_id)
        msgs = self._messages(session, agent_id)
        if not msgs:
            from fastapi import HTTPException

            raise HTTPException(409, "no history to summarize")
        new_history = await self._summarize(session, spec, msgs)
        self._set_messages(session, agent_id, new_history)
        return render_messages(new_history)

    async def _summarize(
        self, session: "Session", spec: "AgentSpec", messages: list[ModelMessage]
    ) -> list[ModelMessage]:
        from ..providers.registry import thinking_settings

        summarizer = Agent(
            model=GatedModel(self._resolve(spec.model), session.gateway),
            instructions=spec.persona or f"You are {spec.name}.",
            model_settings=thinking_settings(spec.model, spec.thinking, spec.thinking_effort),
        )
        r = await summarizer.run(SUMMARIZE_PROMPT, message_history=messages)
        summary = str(r.output).strip()
        return [
            ModelRequest(parts=[UserPromptPart(content=(
                "[Conversation compacted — summary of all prior work]\n\n" + summary
            ))]),
            ModelResponse(parts=[TextPart(content="Understood — I'll continue from this summary.")]),
        ]

    # --- questions / usage ---------------------------------------------------

    def list_questions(self, session: "Session") -> list[dict]:
        return session.questions.list_open()

    def answer_question(self, session: "Session", question_id: str, answers: list[str]) -> bool:
        return session.questions.answer(question_id, answers)

    def usage(self, session: "Session", agent_id: str) -> dict:
        return session.usage.get(agent_id)
