"""Task endpoints: create, list, inspect, retry."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException

from .. import wiring
from .schemas import NewTaskRequest


def install(app: FastAPI) -> None:
    def _spawn_run(session, task_id: str) -> None:
        """Drive a task in the background, keeping a strong reference so the
        run isn't garbage-collected mid-flight."""
        runner = wiring.make_task_runner(app, session)
        t = asyncio.create_task(runner.run(task_id))
        app.state.task_runs.add(t)
        t.add_done_callback(app.state.task_runs.discard)

    @app.get("/api/tasks")
    def list_tasks(session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        return {"tasks": [t.model_dump() for t in app.state.tasks.list_for_session(session.id)]}

    @app.post("/api/tasks")
    async def create_task(body: NewTaskRequest, session_id: str | None = None) -> dict:
        session = wiring.resolve_session(app, session_id)
        agent_id = body.assigned_agent_id or wiring.default_entry_point(session)
        if wiring.find_spec(session, agent_id) is None:
            raise HTTPException(404, f"no agent '{agent_id}' to assign the task to")
        task = app.state.tasks.create(
            session_id=session.id,
            title=body.title or body.prompt[:60],
            prompt=body.prompt,
            assigned_agent_id=agent_id,
            completion_signal=body.completion_signal,
            timeout_hours=body.timeout_hours,
        )
        _spawn_run(session, task.id)
        return task.model_dump()

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        task = app.state.tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "no such task")
        return task.model_dump()

    @app.post("/api/tasks/{task_id}/retry")
    async def retry_task(task_id: str) -> dict:
        """Re-run a blocked task in place: clear the stale result and hand the
        same row back to a fresh TaskRunner (whose first move is blocked →
        running, a legal lifecycle transition). No copy/re-create needed."""
        task = app.state.tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "no such task")
        if task.status != "blocked":
            raise HTTPException(409, f"only blocked tasks can be retried (status is '{task.status}')")
        session = app.state.sessions.get(task.session_id)
        if session is None:
            raise HTTPException(409, "the task's session is not live; resume it first")
        if wiring.find_spec(session, task.assigned_agent_id) is None:
            raise HTTPException(409, f"assigned agent '{task.assigned_agent_id}' is no longer in the team")
        app.state.tasks.set_result(task_id, "")
        _spawn_run(session, task_id)
        return {"status": "retrying", "task_id": task_id}
