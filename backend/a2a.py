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

import sqlite3
from typing import Callable

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.usage import UsageLimits

from .models_domain import AgentSpec, TeamGraph
from .todos import AgentDeps
from .util import iso_now, new_id

MAX_DELEGATION_DEPTH = 3
"""Hard cap on delegation chain length — a code-level loop guard."""


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
        "You can consult these teammates with `ask_agent(target_id, question)`, "
        "passing the id in backticks as target_id. "
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
    """Resolves and runs delegations within one session. ``agent_factory`` builds
    a Pydantic AI agent for a spec (injected so tests control the models)."""

    def __init__(
        self,
        session,
        agent_factory: Callable[[AgentSpec], Agent],
        *,
        message_log: MessageLog | None = None,
        max_depth: int = MAX_DELEGATION_DEPTH,
    ):
        self.session = session
        self._factory = agent_factory
        self._log = message_log
        self._max_depth = max_depth

    def neighbors(self, agent_id: str) -> list[tuple[str, str]]:
        return neighbor_list(self.session.graph, agent_id)

    def _spec(self, agent_id: str) -> AgentSpec | None:
        for node in self.session.graph.nodes:
            if node.spec.id == agent_id:
                return node.spec
        return None

    async def ask(self, *, asker_id: str, target_id: str, question: str, usage, chain: list[str]) -> str:
        allowed = {t for t, _ in self.neighbors(asker_id)}
        if target_id not in allowed:
            raise ModelRetry(
                f"'{target_id}' is not someone you can consult. "
                f"You may ask: {sorted(allowed) or 'no one'}."
            )
        # Cycle + depth guards (the inoculation against runaway delegation loops).
        if target_id in chain or target_id == asker_id:
            raise ModelRetry(f"delegation cycle: '{target_id}' is already in the current chain {chain}.")
        if len(chain) >= self._max_depth:
            raise ModelRetry(f"delegation depth cap ({self._max_depth}) reached; answer directly.")

        target_spec = self._spec(target_id)
        if target_spec is None:
            raise ModelRetry(f"no agent '{target_id}' exists.")

        self._publish(asker_id, target_id, "question", question)
        target_agent = self._factory(target_spec)
        child_deps = AgentDeps(
            session_id=self.session.id,
            agent_id=target_id,
            delegator=self,
            delegation_chain=chain + [asker_id],
        )
        result = await target_agent.run(
            question,
            deps=child_deps,
            usage=usage,
            usage_limits=UsageLimits(request_limit=50),
        )
        answer = str(result.output)
        self._publish(target_id, asker_id, "reply", answer)
        return answer

    def _publish(self, frm: str, to: str, kind: str, body: str) -> None:
        self.session.bus.publish(
            "a2a_message", {"from": frm, "to": to, "kind": kind, "body": body}
        )
        if self._log is not None:
            self._log.record(self.session.id, frm, to, kind, body)


# --- the tool ---------------------------------------------------------------


async def ask_agent(ctx: RunContext[AgentDeps], target_id: str, question: str) -> str:
    """Consult a teammate. ``target_id`` must be one of your listed neighbors.
    Returns their concise answer. Use this instead of guessing about a domain a
    teammate owns."""
    if ctx.deps.delegator is None:
        raise ModelRetry("delegation is not available in this context.")
    return await ctx.deps.delegator.ask(
        asker_id=ctx.deps.agent_id,
        target_id=target_id,
        question=question,
        usage=ctx.usage,
        chain=ctx.deps.delegation_chain,
    )
