"""The task system: first-class work items with a status lifecycle and per-task
completion gates.

You are the orchestrator at design time — the graph *is* the org chart — so the
runtime doesn't discover the team; it routes a tracked Task to an entry-point
agent and gates its completion. This module has three parts:

1. **Pure state machine** — allowed transitions + signal parsing. Trivially
   tested, no I/O.
2. **`TaskStore`** — CRUD over the ``tasks`` table.
3. **`TaskRunner`** — orchestrates: run the assigned agent, then apply the
   per-task completion signal (self_reported / reviewer:<id> / check:<cmd>),
   looping needs_revision→running with the critique injected, bounded by a hard
   revision cap that lands a stuck task in ``blocked`` (never an infinite loop).

The runner takes its effectful steps (run an agent, run a reviewer, run a shell
check) as **injected callables**, so the orchestration logic is tested
deterministically without any model or subprocess.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel

from ..domain.models import Task, TaskStatus, Todo
from ..util import iso_now, new_id

MAX_REVISION_ROUNDS = 3
"""Reviewer/check ping-pong cap — exceeding it parks the task in ``blocked``."""


# --- pure state machine -----------------------------------------------------

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"needs_review", "blocked", "failed", "cancelled"},
    "needs_review": {"done", "needs_revision", "blocked"},
    "needs_revision": {"running", "cancelled"},
    "blocked": {"running", "cancelled", "failed"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
}


def validate_transition(current: str, target: str) -> bool:
    """True if ``current → target`` is a legal lifecycle move."""
    return target in ALLOWED_TRANSITIONS.get(current, set())


def parse_completion_signal(signal: str) -> tuple[str, str]:
    """Parse a completion signal into ``(kind, arg)``.

    ``self_reported`` → ``("self_reported", "")``;
    ``reviewer:<agent_id>`` → ``("reviewer", "<agent_id>")``;
    ``check:<command>`` → ``("check", "<command>")``.
    """
    if signal.startswith("reviewer:"):
        return "reviewer", signal[len("reviewer:") :]
    if signal.startswith("check:"):
        return "check", signal[len("check:") :]
    return "self_reported", ""


class ReviewVerdict(BaseModel):
    """A reviewer agent's structured judgment (the evaluator-optimizer output)."""

    approved: bool
    critique: str = ""


# --- store ------------------------------------------------------------------


class TaskStore:
    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], str] = iso_now):
        self._conn = conn
        self._now = clock

    def create(
        self,
        *,
        session_id: str,
        title: str,
        prompt: str,
        assigned_agent_id: str,
        completion_signal: str = "self_reported",
        timeout_hours: float = 1.0,
        parent_task_id: str | None = None,
        delegation_chain: list[str] | None = None,
    ) -> Task:
        task = Task(
            id=new_id("task_"),
            session_id=session_id,
            title=title,
            prompt=prompt,
            assigned_agent_id=assigned_agent_id,
            completion_signal=completion_signal,
            timeout_hours=timeout_hours,
            parent_task_id=parent_task_id,
            delegation_chain=delegation_chain or [],
            created_at=self._now(),
            updated_at=self._now(),
        )
        self._conn.execute(
            """INSERT INTO tasks (id, session_id, title, prompt, assigned_agent_id, status,
                   completion_signal, timeout_hours, todos, parent_task_id, delegation_chain, result,
                   created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task.id, task.session_id, task.title, task.prompt, task.assigned_agent_id,
                task.status, task.completion_signal, task.timeout_hours, "[]", task.parent_task_id,
                json.dumps(task.delegation_chain), "", task.created_at, task.updated_at,
            ),
        )
        self._conn.commit()
        return task

    def get(self, task_id: str) -> Task | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row(row) if row else None

    def list_for_session(self, session_id: str) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at", (session_id,)
        ).fetchall()
        return [self._row(r) for r in rows]

    def set_status(self, task_id: str, status: TaskStatus) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, self._now(), task_id),
        )
        self._conn.commit()

    def set_result(self, task_id: str, result: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET result = ?, updated_at = ? WHERE id = ?",
            (result, self._now(), task_id),
        )
        self._conn.commit()

    def set_todos(self, task_id: str, todos: list[Todo]) -> None:
        self._conn.execute(
            "UPDATE tasks SET todos = ?, updated_at = ? WHERE id = ?",
            (json.dumps([t.model_dump() for t in todos]), self._now(), task_id),
        )
        self._conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            prompt=row["prompt"],
            assigned_agent_id=row["assigned_agent_id"],
            status=row["status"],
            completion_signal=row["completion_signal"],
            timeout_hours=row["timeout_hours"],
            todos=[Todo(**t) for t in json.loads(row["todos"])],
            parent_task_id=row["parent_task_id"],
            delegation_chain=json.loads(row["delegation_chain"]),
            result=row["result"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def run_check(command: str, cwd: Path) -> tuple[int, str]:
    """Run a deterministic completion gate (pytest/npm test/build) in the repo."""
    r = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=str(cwd))
    return r.returncode, (r.stdout + r.stderr)[-4000:]


# --- runner -----------------------------------------------------------------

AgentRunner = Callable[[str, str], Awaitable[str]]          # (agent_id, prompt) -> output
ReviewerRunner = Callable[[str, str, str], Awaitable[ReviewVerdict]]  # (reviewer_id, prompt, result)
CheckRunner = Callable[[str], tuple[int, str]]              # (command) -> (code, output)


class TaskRunner:
    """Orchestrates one task to a terminal state, applying its completion gate.

    Effectful steps are injected so the logic is tested without models/subprocess:
    ``run_agent`` runs the assigned agent on a prompt; ``run_reviewer`` runs a
    reviewer agent; ``run_check`` runs a shell command.
    """

    def __init__(
        self,
        store: TaskStore,
        *,
        run_agent: AgentRunner,
        run_reviewer: ReviewerRunner,
        run_check: CheckRunner,
        publish: Callable[[str, dict], None] | None = None,
        max_revision_rounds: int = MAX_REVISION_ROUNDS,
    ):
        self._store = store
        self._run_agent = run_agent
        self._run_reviewer = run_reviewer
        self._run_check = run_check
        self._publish = publish or (lambda _t, _d: None)
        self._max_rounds = max_revision_rounds

    def _to(self, task_id: str, status: TaskStatus) -> None:
        self._store.set_status(task_id, status)
        self._publish("task_status", {"task_id": task_id, "status": status})

    async def run(self, task_id: str) -> str:
        """Drive the task to a terminal status. Returns the final status."""
        task = self._store.get(task_id)
        if task is None:
            raise ValueError(f"no task {task_id}")
        kind, arg = parse_completion_signal(task.completion_signal)
        prompt = task.prompt
        rounds = 0

        # Per-task wall-clock budget (hours → seconds); 0/None means no limit. On
        # timeout, asyncio.wait_for cancels the agent run (the opencode harness
        # aborts the in-flight OC run + the whole delegation subtree on that
        # CancelledError) and we park the task blocked, Retry-able.
        timeout_s = (task.timeout_hours or 0) * 3600 or None

        while True:
            self._to(task_id, "running")
            try:
                output = await asyncio.wait_for(
                    self._run_agent(task.assigned_agent_id, prompt), timeout=timeout_s
                )
            except asyncio.TimeoutError:
                self._store.set_result(
                    task_id,
                    f"[timed out after {task.timeout_hours:g}h — raise the task's timeout "
                    "or split the work into smaller tasks, then press Retry]",
                )
                self._to(task_id, "blocked")
                return "blocked"
            except asyncio.CancelledError:
                # the user pressed Stop on the agent mid-run — park the task
                # where Retry can revive it, then let the cancellation proceed
                self._store.set_result(task_id, "[stopped by the user — press Retry to run it again]")
                self._to(task_id, "blocked")
                raise
            except Exception as e:  # noqa: BLE001 — caps/errors park the task
                self._store.set_result(task_id, f"error: {e}")
                self._to(task_id, "blocked")
                return "blocked"

            self._store.set_result(task_id, output)
            self._to(task_id, "needs_review")

            if kind == "self_reported":
                self._to(task_id, "done")
                return "done"

            if kind == "check":
                code, out = self._run_check(arg)
                if code == 0:
                    self._to(task_id, "done")
                    return "done"
                feedback = f"The check `{arg}` failed (exit {code}):\n{out}"
            else:  # reviewer
                verdict = await self._run_reviewer(arg, task.prompt, output)
                if verdict.approved:
                    self._to(task_id, "done")
                    return "done"
                feedback = f"A reviewer rejected the work:\n{verdict.critique}"

            rounds += 1
            if rounds >= self._max_rounds:
                self._store.set_result(task_id, feedback)
                self._to(task_id, "blocked")  # stop the ping-pong; ask for attention
                return "blocked"
            self._to(task_id, "needs_revision")
            prompt = f"{feedback}\n\nRevise your previous work to address this."
            self._to(task_id, "running")  # loop continues
