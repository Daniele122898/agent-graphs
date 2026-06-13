"""OpenCode-backed harness.

Drives a headless ``opencode serve`` process (one per session) and translates
its HTTP/SSE surface into the ``Harness`` interface + our event bus. Built up
across phases:

- ``config.py`` (Phase 2) — generate ``opencode.json`` + the ask_agent tool
  from a ``TeamGraph``. **DONE.**
- ``server.py`` (Phase 2) — spawn/own the server process per session.
- ``client.py`` + this harness (Phase 3+) — sessions, prompts, SSE→bus
  translation, history, usage, ask_user, ask_agent.

Until the later phases land, the harness raises ``NotImplementedError`` for
the not-yet-built operations; nothing selects it by default (config/UI default
is ``native``), so it stays dormant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Harness, HistoryView
from .config import build_opencode_config

if TYPE_CHECKING:
    from ...agents.a2a import MessageLog
    from ...runtime.sessions import Session
    from ...runtime.tasks import ReviewVerdict
    from ...storage.agent_state import AgentStateStore


class OpenCodeHarness(Harness):
    id = "opencode"

    def __init__(
        self,
        *,
        state_store: "AgentStateStore",
        message_log: "MessageLog",
        repo_root=None,
    ):
        self._state = state_store
        self.message_log = message_log
        self._repo_root = repo_root

    # Phases 3–5 implement these against the OpenCode server.
    async def submit(self, session: "Session", agent_id: str, prompt: str) -> None:
        raise NotImplementedError("OpenCodeHarness.submit — Phase 3")

    async def run_to_completion(
        self, session, agent_id, prompt, *, usage=None, delegation_chain=None, lock_timeout=None
    ) -> str:
        raise NotImplementedError("OpenCodeHarness.run_to_completion — Phase 3")

    async def run_for_task(self, session, agent_id, prompt) -> str:
        raise NotImplementedError("OpenCodeHarness.run_for_task — Phase 3")

    async def run_reviewer(self, session, reviewer_id, task_prompt, result) -> "ReviewVerdict":
        raise NotImplementedError("OpenCodeHarness.run_reviewer — Phase 3")

    async def stop(self, session, agent_id) -> None:
        raise NotImplementedError("OpenCodeHarness.stop — Phase 3")

    def is_busy(self, session, agent_id) -> bool:
        raise NotImplementedError("OpenCodeHarness.is_busy — Phase 3")

    async def history(self, session, agent_id) -> HistoryView:
        raise NotImplementedError("OpenCodeHarness.history — Phase 3")

    async def clear_history(self, session, agent_id) -> None:
        raise NotImplementedError("OpenCodeHarness.clear_history — Phase 3")

    async def summarize_history(self, session, agent_id) -> list[dict]:
        raise NotImplementedError("OpenCodeHarness.summarize_history — Phase 3")

    def list_questions(self, session) -> list[dict]:
        raise NotImplementedError("OpenCodeHarness.list_questions — Phase 4")

    def answer_question(self, session, question_id, answers) -> bool:
        raise NotImplementedError("OpenCodeHarness.answer_question — Phase 4")

    def usage(self, session, agent_id) -> dict:
        raise NotImplementedError("OpenCodeHarness.usage — Phase 3")


__all__ = ["OpenCodeHarness", "build_opencode_config"]
