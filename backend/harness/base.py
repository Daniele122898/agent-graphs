"""The ``Harness`` interface + shared, harness-independent delegation guards.

Every agent operation the product performs goes through a ``Harness`` method
keyed by ``agent_id`` (never a harness-specific worker handle), so the HTTP
layer and ``wiring`` are identical regardless of which harness backs a session.

``delegate()`` is implemented once here (the orchestration of ask_agent —
guards, lifecycle, message log, run the target — is harness-independent given a
``run_to_completion``); subclasses only differ in how a single agent run
executes. The delegation *guards* (must be a graph neighbor, no cycles, bounded
depth) are pure and shared via ``check_delegation``.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic_ai import ModelRetry

from ..domain.models import AgentLifecycle, AgentSpec, TeamGraph

if TYPE_CHECKING:
    from ..agents.a2a import MessageLog
    from ..runtime.sessions import Session
    from ..runtime.tasks import ReviewVerdict

# Delegation caps (mirrors a2a.py; the single source for both harnesses).
MAX_DELEGATION_DEPTH = 3
DELEGATION_BUSY_TIMEOUT = 15 * 60.0
# Max teammates one agent may fan work out to in a single ask_team call. Bounds
# concurrent target runs (the weak local model / single LM Studio can choke on
# too many at once); split larger work across turns.
MAX_FANOUT = 4


@dataclass
class HistoryView:
    """The control room's view of an agent's model context: the system sections
    sent with every request + the stored conversation rendered as rows + count.
    Both harnesses produce this identical shape so the UI renders the same."""

    instructions: list[str]
    rows: list[dict]
    message_count: int

    def payload(self) -> dict:
        return {
            "instructions": self.instructions,
            "rows": self.rows,
            "message_count": self.message_count,
        }


def neighbors_of(graph: TeamGraph, agent_id: str) -> set[str]:
    return {e.target for e in graph.neighbors_of(agent_id)}


def find_spec(graph: TeamGraph, agent_id: str) -> AgentSpec | None:
    for node in graph.nodes:
        if node.spec.id == agent_id:
            return node.spec
    return None


def resolve_target(graph: TeamGraph, asker_id: str, target_ref: str) -> str | None:
    """Resolve a delegation target given by id OR display name to the canonical
    neighbor id. Case-insensitive, trimmed, surrounding backticks stripped (the
    instructions show ids as ``Planner (`agent_6`)`` and small models copy either
    the name or the backticked id verbatim). Scoped to the asker's neighbors so
    resolution and the neighbor guard share ONE set — you can never resolve to a
    non-neighbor. Returns the canonical id, or None if nothing matches; raises
    ``ModelRetry`` (a self-correcting nudge, never a fatal error) on an ambiguous
    name. Pure — shared by both harnesses."""
    ref = (target_ref or "").strip().strip("`").strip()
    allowed = neighbors_of(graph, asker_id)
    if ref in allowed:  # exact id — unchanged fast path, today's behavior
        return ref
    low = ref.casefold()
    id_hits = [a for a in allowed if a.casefold() == low]
    if len(id_hits) == 1:
        return id_hits[0]
    names = {n.spec.id: n.spec.name for n in graph.nodes}
    name_hits = sorted({a for a in allowed if (names.get(a) or "").strip().casefold() == low})
    if len(name_hits) == 1:
        return name_hits[0]
    if len(name_hits) > 1:
        raise ModelRetry(
            f"'{ref}' is ambiguous — it matches teammates {name_hits}. "
            "Pass the exact id in backticks."
        )
    return None


def _consultable(graph: TeamGraph, asker_id: str) -> list[str]:
    """Pretty ``Name (`id`)`` list of who an asker may consult, for error nudges."""
    names = {n.spec.id: n.spec.name for n in graph.nodes}
    return sorted(f"{names.get(a, a)} (`{a}`)" for a in neighbors_of(graph, asker_id))


def check_delegation(
    graph: TeamGraph, asker_id: str, target_id: str, chain: list[str], *, max_depth: int = MAX_DELEGATION_DEPTH
) -> AgentSpec:
    """Validate an ask_agent call against the graph + current delegation chain.
    Accepts a target by id OR display name (resolved to the canonical id BEFORE
    the cycle/depth guards, so a name typed once and the id next hop can't dodge
    the cycle guard). Raises ``ModelRetry`` (a self-correcting nudge) on any
    violation; returns the target's spec on success (``spec.id`` is the canonical
    target id). Pure — identical for every harness."""
    resolved = resolve_target(graph, asker_id, target_id)  # may raise ModelRetry (ambiguous)
    if resolved is None:
        raise ModelRetry(
            f"'{target_id}' is not someone you can consult. "
            f"You may ask: {_consultable(graph, asker_id) or 'no one'}."
        )
    target_id = resolved
    if target_id in chain or target_id == asker_id:
        raise ModelRetry(f"delegation cycle: '{target_id}' is already in the current chain {chain}.")
    if len(chain) >= max_depth:
        raise ModelRetry(f"delegation depth cap ({max_depth}) reached; answer directly.")
    spec = find_spec(graph, target_id)
    if spec is None:
        raise ModelRetry(f"no agent '{target_id}' exists.")
    return spec


class Harness(ABC):
    """Backs a session's agents. Stateless w.r.t. a session except for resources
    the subclass owns (workers, a server); every method takes ``session``.

    The optional ``message_log`` lets ``delegate`` persist inter-agent messages.
    """

    id: str = "base"
    message_log: "MessageLog | None" = None

    # --- bring-up / teardown -------------------------------------------------

    async def start(self, session: "Session") -> None:
        """Optional: bring the harness up for this session (opencode: spawn the
        server). Idempotent. Default: nothing."""

    async def shutdown(self, session: "Session") -> None:
        """Tear down (stop workers / kill the server). Idempotent."""

    # --- execution -----------------------------------------------------------

    @abstractmethod
    async def submit(self, session: "Session", agent_id: str, prompt: str) -> None:
        """Queue a prompt (run if idle, interject if busy). Returns immediately;
        the run streams to the session bus."""

    @abstractmethod
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
        """Run one prompt to completion and return the output. Used by
        delegation (``delegate``) and indirectly by the task runner."""

    @abstractmethod
    async def run_for_task(self, session: "Session", agent_id: str, prompt: str) -> str:
        """Run a task turn to completion WITH the open-todos continuation nudge
        (the anti-stall layer). Used by the TaskRunner."""

    @abstractmethod
    async def run_reviewer(
        self, session: "Session", reviewer_id: str, task_prompt: str, result: str
    ) -> "ReviewVerdict":
        """Run a reviewer agent with structured ReviewVerdict output (completion gate)."""

    @abstractmethod
    async def stop(self, session: "Session", agent_id: str) -> None:
        """Cancel any in-flight run for this agent. Idempotent."""

    @abstractmethod
    def is_busy(self, session: "Session", agent_id: str) -> bool:
        """True while a run is in flight (history mutation is refused then)."""

    # --- history / context ---------------------------------------------------

    @abstractmethod
    async def history(self, session: "Session", agent_id: str) -> HistoryView:
        """The agent's system context + rendered transcript + message count."""

    @abstractmethod
    async def clear_history(self, session: "Session", agent_id: str) -> None:
        """Wipe the conversation (identity/instructions are rebuilt per run)."""

    @abstractmethod
    async def summarize_history(self, session: "Session", agent_id: str) -> list[dict]:
        """Compact the conversation via a model call; return the new rendered rows."""

    # --- questions (ask_user) ------------------------------------------------

    @abstractmethod
    def list_questions(self, session: "Session") -> list[dict]:
        """All unanswered ask_user questions in the session."""

    @abstractmethod
    async def answer_question(self, session: "Session", question_id: str, answers: list[str]) -> bool:
        """Resolve a pending question; False if unknown. Raises ValueError on a
        question/answer count mismatch."""

    # --- usage ---------------------------------------------------------------

    @abstractmethod
    def usage(self, session: "Session", agent_id: str) -> dict:
        """Per-agent ``{requests, input_tokens, output_tokens}``."""

    # --- delegation (ask_agent) — concrete, shared ---------------------------

    async def delegate(
        self,
        session: "Session",
        asker_id: str,
        target_id: str,
        question: str,
        *,
        usage=None,
        chain: list[str] | None = None,
    ) -> str:
        """Consult a teammate: validate guards, mark the asker waiting, run the
        target to completion on its persistent worker, return the answer. Shared
        by both harnesses (the native in-process ``ask_agent`` tool has its own
        equivalent path via ``Delegator``; this serves the opencode callback and
        any harness-level delegation)."""
        chain = list(chain or [])
        spec = check_delegation(session.graph, asker_id, target_id, chain)  # raises ModelRetry
        target_id = spec.id  # canonical id (target_id may have been a display name)
        self._set_lifecycle(session, asker_id, "waiting-on-agent")
        try:
            return await self._consult_one(
                session, asker_id, target_id, question, usage=usage, chain=chain + [asker_id]
            )
        finally:
            self._set_lifecycle(session, asker_id, "running")

    async def delegate_many(
        self,
        session: "Session",
        asker_id: str,
        requests: list[tuple[str, str]],
        *,
        usage=None,
        chain: list[str] | None = None,
    ) -> str:
        """Fan out to several teammates AT ONCE (the planner→frontend+backend
        case). Validates every (target, question) up front against the SAME chain
        snapshot, then runs them concurrently and returns their answers together.
        The asker makes ONE waiting→running transition; a per-target failure
        becomes an inline note rather than aborting its siblings. Shared by both
        harnesses (the opencode ask_team callback; native has its own ask_many)."""
        chain = list(chain or [])
        if not requests:
            raise ModelRetry("ask_team needs at least one (teammate, task) pair.")
        if len(requests) > MAX_FANOUT:
            raise ModelRetry(
                f"ask_team is capped at {MAX_FANOUT} teammates at once; split the work across turns."
            )
        # Resolve + guard EVERY target before running any (all against the same
        # pre-fan-out chain), and reject a duplicate target — one teammate does
        # one thing at a time, two batch entries would contend on its lock.
        resolved: list[tuple[str, str]] = []
        seen: set[str] = set()
        for target_ref, question in requests:
            spec = check_delegation(session.graph, asker_id, target_ref, chain)  # raises ModelRetry
            if spec.id in seen:
                raise ModelRetry(
                    f"'{spec.id}' is listed twice — one teammate does one thing at a time; ask them once."
                )
            seen.add(spec.id)
            resolved.append((spec.id, question))

        async def _one(tid: str, q: str) -> tuple[str, str]:
            try:
                return tid, await self._consult_one(
                    session, asker_id, tid, q, usage=usage, chain=chain + [asker_id]
                )
            except ModelRetry as e:  # isolate: a failed teammate must not sink the others
                return tid, f"[consulting {tid} failed: {e}]"

        self._set_lifecycle(session, asker_id, "waiting-on-agent")
        try:
            results = await asyncio.gather(*(_one(tid, q) for tid, q in resolved))
        finally:
            self._set_lifecycle(session, asker_id, "running")
        names = {n.spec.id: n.spec.name for n in session.graph.nodes}
        return "\n\n".join(f"From {names.get(tid, tid)} (`{tid}`):\n{ans}" for tid, ans in results)

    async def _consult_one(
        self, session: "Session", asker_id: str, target_id: str, question: str, *, usage, chain: list[str]
    ) -> str:
        """Run ONE already-resolved target to completion + record the Q/reply.
        Does NOT touch the asker's lifecycle — ``delegate``/``delegate_many`` own
        that single waiting→running transition. ``chain`` is the FULL chain for
        the child (caller appends the asker). Raises ``ModelRetry`` on busy/failure
        (recording the failure as the reply so the canvas shows WHY)."""
        self._record(session, asker_id, target_id, "question", question)
        try:
            answer = await self.run_to_completion(
                session, target_id, question, usage=usage,
                delegation_chain=chain, lock_timeout=DELEGATION_BUSY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self._record(session, target_id, asker_id, "reply", f"[no reply: busy for over {int(DELEGATION_BUSY_TIMEOUT)}s]")
            raise ModelRetry(
                f"'{target_id}' has been busy for over {int(DELEGATION_BUSY_TIMEOUT)}s; "
                "proceed without them or try again later."
            ) from None
        except ModelRetry:
            raise
        except Exception as e:  # noqa: BLE001 — surfaced on the target as agent_error too
            self._record(session, target_id, asker_id, "reply", f"[consultation failed: {e}]")
            raise ModelRetry(f"consulting '{target_id}' failed ({e}); handle it without them.") from e
        self._record(session, target_id, asker_id, "reply", answer)
        return answer

    # --- shared event helpers (publish to the universal bus + registry) ------

    def _set_lifecycle(self, session: "Session", agent_id: str, lifecycle: AgentLifecycle) -> None:
        session.registry.set_lifecycle(agent_id, lifecycle)
        session.bus.publish("agent_lifecycle", {"agent_id": agent_id, "lifecycle": lifecycle})

    def _record(self, session: "Session", frm: str, to: str, kind: str, body: str) -> None:
        session.bus.publish("a2a_message", {"from": frm, "to": to, "kind": kind, "body": body})
        if self.message_log is not None:
            self.message_log.record(session.id, frm, to, kind, body)
