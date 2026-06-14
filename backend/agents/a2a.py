"""Agent-to-agent communication: a delegation ``ask_agent`` tool.

The crux feature. Rather than a heavyweight protocol (A2A/MCP solve cross-vendor
interop we don't have), an in-process ``ask_agent(target_id, question)`` tool
lets an agent consult a neighbor. The target runs with its *own* persona and
capabilities, answers concisely, and returns — so the asker's context isn't
polluted by the target's research. Usage is shared (``ctx.usage``) so delegated
work counts against the same budget, and structural guards (must be a graph
neighbor, no cycles, bounded depth) prevent the runaway A→B→C→A loops that sink
most multi-agent systems.

Connection awareness is dynamic: each agent's instructions include its live
graph neighbors (re-evaluated each run via ``@agent.instructions``), so editing
the graph immediately changes who an agent knows to consult.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import TYPE_CHECKING, Awaitable, Callable

from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import UsageLimits

from ..domain.models import AgentSpec, TeamGraph
from .todos import AgentDeps
from ..util import iso_now, new_id

if TYPE_CHECKING:  # runtime/workers.py imports this module; avoid the cycle
    from ..runtime.workers import RunningAgent

MAX_DELEGATION_DEPTH = 3
"""Hard cap on delegation chain length — a code-level loop guard."""

DELEGATION_BUSY_TIMEOUT = 15 * 60.0
"""How long ``ask_agent`` waits for a busy target before giving up (seconds).

A target legitimately busy with another task is *waited for* (one agent = one
person, runs are serialized per worker); the timeout is a backstop against the
A⇄B mutual-delegation deadlock two simultaneous runs could otherwise produce.
"""


# --- pure: neighbor list ----------------------------------------------------


def neighbor_list(graph: TeamGraph, agent_id: str) -> list[tuple[str, str]]:
    """The (target_id, why) pairs an agent may delegate to. Pure."""
    return [(e.target, e.label) for e in graph.neighbors_of(agent_id)]


def neighbor_instructions(graph: TeamGraph, agent_id: str) -> str:
    """The instructions fragment naming who an agent may consult and why. Empty
    if it has no neighbors. Re-evaluated each run so it tracks graph edits.

    Each teammate is listed by human-readable name with the id to pass to
    ``ask_agent`` — the name carries the semantic role (\"Python Expert\"), the
    id is the routing key."""
    pairs = neighbor_list(graph, agent_id)
    if not pairs:
        return ""
    names = {n.spec.id: n.spec.name for n in graph.nodes}
    lines = "\n".join(
        f"- {names.get(t, t)} (`{t}`){f' — {why}' if why else ''}" for t, why in pairs
    )
    return (
        "You can consult these teammates with `ask_agent(target_id, question)` "
        "(pass either the id in backticks or the display name). To hand work to "
        "SEVERAL of them at once, in parallel, call `ask_team` with one assignment "
        "(teammate + task) per teammate instead of asking them one after another. "
        "They answer from their own expertise; use them instead of guessing:\n" + lines
    )


# --- message log ------------------------------------------------------------


class MessageLog:
    """Persists inter-agent questions/replies for live view + later review."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], str] = iso_now):
        self._conn = conn
        self._now = clock

    def record(self, session_id: str, from_agent: str, to_agent: str, kind: str, body: str) -> None:
        self._conn.execute(
            "INSERT INTO messages (id, session_id, from_agent, to_agent, kind, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id("msg_"), session_id, from_agent, to_agent, kind, body, self._now()),
        )
        self._conn.commit()

    def for_session(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- delegator (session-scoped) ---------------------------------------------


class Delegator:
    """Resolves and runs delegations within one session.

    ``worker_provider`` is an async callable ``(spec) -> RunningAgent`` that
    returns the target's *registered* worker (creating one on demand) — so a
    delegated run goes through the same streamed/persisted path as any other
    run: the target's lifecycle badge, transcript, and history all reflect the
    work. Injected so tests control the models behind the workers.
    """

    def __init__(
        self,
        session,
        worker_provider: Callable[[AgentSpec], Awaitable["RunningAgent"]],
        *,
        message_log: MessageLog | None = None,
        max_depth: int = MAX_DELEGATION_DEPTH,
        busy_timeout: float = DELEGATION_BUSY_TIMEOUT,
    ):
        self.session = session
        self._workers = worker_provider
        self._log = message_log
        self._max_depth = max_depth
        self._busy_timeout = busy_timeout

    def neighbors(self, agent_id: str) -> list[tuple[str, str]]:
        return neighbor_list(self.session.graph, agent_id)

    def _spec(self, agent_id: str) -> AgentSpec | None:
        for node in self.session.graph.nodes:
            if node.spec.id == agent_id:
                return node.spec
        return None

    def _validate(self, asker_id: str, target_ref: str, chain: list[str]) -> AgentSpec:
        """Resolve a target (display name OR id) to its spec + run the neighbor /
        cycle / depth guards on the canonical id. Shared resolver with the
        opencode harness (base.resolve_target — local import avoids an
        agents↔harness cycle). Raises ``ModelRetry`` on any violation."""
        from ..harness.base import resolve_target

        resolved = resolve_target(self.session.graph, asker_id, target_ref)  # raises ModelRetry if ambiguous
        if resolved is None:
            names = {n.spec.id: n.spec.name for n in self.session.graph.nodes}
            pretty = sorted(f"{names.get(t, t)} (`{t}`)" for t, _ in self.neighbors(asker_id))
            raise ModelRetry(
                f"'{target_ref}' is not someone you can consult. "
                f"You may ask: {pretty or 'no one'}."
            )
        if resolved in chain or resolved == asker_id:
            raise ModelRetry(f"delegation cycle: '{resolved}' is already in the current chain {chain}.")
        if len(chain) >= self._max_depth:
            raise ModelRetry(f"delegation depth cap ({self._max_depth}) reached; answer directly.")
        spec = self._spec(resolved)
        if spec is None:
            raise ModelRetry(f"no agent '{resolved}' exists.")
        return spec

    async def _run_one(self, asker_id: str, target_spec: AgentSpec, question: str, usage, chain: list[str]) -> str:
        """Run ONE already-validated target through its real RunningAgent (streamed
        events, shared history) + record the Q/reply. Does NOT touch the asker's
        lifecycle (ask/ask_many own that). ``chain`` is the FULL child chain."""
        target_id = target_spec.id
        self._publish(asker_id, target_id, "question", question)
        try:
            worker = await self._workers(target_spec)
            answer = await worker.run_once(
                question,
                usage=usage,
                usage_limits=UsageLimits(request_limit=50),
                delegation_chain=chain,
                lock_timeout=self._busy_timeout,
            )
        except asyncio.TimeoutError:
            self._publish(target_id, asker_id, "reply", f"[no reply: busy for over {int(self._busy_timeout)}s]")
            raise ModelRetry(
                f"'{target_id}' has been busy for over {int(self._busy_timeout)}s; "
                "proceed without them or try again later."
            ) from None
        except ModelRetry:
            raise
        except Exception as e:  # noqa: BLE001 — surfaced on the target as agent_error too
            # Record the failure as the reply so the canvas + message log show
            # WHY the consultation died, not just a generic retries-exceeded.
            self._publish(target_id, asker_id, "reply", f"[consultation failed: {e}]")
            raise ModelRetry(f"consulting '{target_id}' failed ({e}); handle it without them.") from e
        self._publish(target_id, asker_id, "reply", answer)
        return answer

    async def ask(self, *, asker_id: str, target_id: str, question: str, usage, chain: list[str]) -> str:
        spec = self._validate(asker_id, target_id, chain)
        # The asker is visibly waiting, the target visibly working: delegation
        # runs through the target's RunningAgent (streamed events, shared
        # history, persisted state), not an invisible throwaway agent.
        self._set_lifecycle(asker_id, "waiting-on-agent")
        try:
            return await self._run_one(asker_id, spec, question, usage, list(chain) + [asker_id])
        finally:
            self._set_lifecycle(asker_id, "running")

    async def ask_many(self, *, asker_id: str, requests: list[tuple[str, str]], usage, chain: list[str]) -> str:
        """Fan out to several teammates concurrently (planner→frontend+backend).
        Validates all up front against one chain snapshot, rejects duplicate
        targets, runs them in parallel, and returns their answers together — the
        asker makes ONE waiting→running transition; a per-target failure is an
        inline note, never aborting siblings."""
        from ..harness.base import MAX_FANOUT

        chain = list(chain or [])
        if not requests:
            raise ModelRetry("ask_team needs at least one (teammate, task) pair.")
        if len(requests) > MAX_FANOUT:
            raise ModelRetry(f"ask_team is capped at {MAX_FANOUT} teammates at once; split the work across turns.")
        specs: list[tuple[AgentSpec, str]] = []
        seen: set[str] = set()
        for target_ref, question in requests:
            spec = self._validate(asker_id, target_ref, chain)
            if spec.id in seen:
                raise ModelRetry(f"'{spec.id}' is listed twice — one teammate does one thing at a time; ask them once.")
            seen.add(spec.id)
            specs.append((spec, question))

        async def _one(spec: AgentSpec, q: str) -> tuple[str, str]:
            try:
                return spec.id, await self._run_one(asker_id, spec, q, usage, chain + [asker_id])
            except ModelRetry as e:
                return spec.id, f"[consulting {spec.id} failed: {e}]"

        self._set_lifecycle(asker_id, "waiting-on-agent")
        try:
            results = await asyncio.gather(*(_one(spec, q) for spec, q in specs))
        finally:
            self._set_lifecycle(asker_id, "running")
        names = {n.spec.id: n.spec.name for n in self.session.graph.nodes}
        return "\n\n".join(f"From {names.get(tid, tid)} (`{tid}`):\n{ans}" for tid, ans in results)

    def _set_lifecycle(self, agent_id: str, lifecycle: str) -> None:
        self.session.registry.set_lifecycle(agent_id, lifecycle)
        self.session.bus.publish("agent_lifecycle", {"agent_id": agent_id, "lifecycle": lifecycle})

    def _publish(self, frm: str, to: str, kind: str, body: str) -> None:
        self.session.bus.publish(
            "a2a_message", {"from": frm, "to": to, "kind": kind, "body": body}
        )
        if self._log is not None:
            self._log.record(self.session.id, frm, to, kind, body)


# --- the tool ---------------------------------------------------------------


async def ask_agent(ctx: RunContext[AgentDeps], target_id: str, question: str) -> str:
    """Consult a teammate. ``target_id`` is one of your listed neighbors — pass
    either its id (in backticks) or its display name; both resolve. Returns their
    concise answer. Use this instead of guessing about a domain a teammate owns."""
    if ctx.deps.delegator is None:
        raise ModelRetry("delegation is not available in this context.")
    return await ctx.deps.delegator.ask(
        asker_id=ctx.deps.agent_id,
        target_id=target_id,
        question=question,
        usage=ctx.usage,
        chain=ctx.deps.delegation_chain,
    )


class TeamAssignment(BaseModel):
    """One teammate + the task to hand them, for ``ask_team`` fan-out."""

    target_id: str
    """The teammate to delegate to — their id (in backticks) or display name."""
    task: str
    """The task/question for that teammate."""


async def ask_team(ctx: RunContext[AgentDeps], assignments: list[TeamAssignment]) -> str:
    """Delegate to SEVERAL teammates AT ONCE, in parallel, and get all their
    answers back together. Each assignment names a teammate (their listed id or
    display name) and the task for them. Use this to fan out independent work —
    e.g. a frontend task and a backend task simultaneously — instead of asking one
    teammate, waiting, then the next. Only your listed neighbors are reachable."""
    if ctx.deps.delegator is None:
        raise ModelRetry("delegation is not available in this context.")
    pairs = [(a.target_id, a.task) for a in assignments]
    return await ctx.deps.delegator.ask_many(
        asker_id=ctx.deps.agent_id,
        requests=pairs,
        usage=ctx.usage,
        chain=ctx.deps.delegation_chain,
    )
