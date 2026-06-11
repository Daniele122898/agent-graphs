"""Domain data shapes for the multi-agent system.

These are *data only* — no behavior that touches I/O, the model, or the
filesystem. They are the spine of the data model: a Team is a reusable
definition, a Session is a running instance bound to a repo, and a Task is a
unit of work that flows along the graph. Everything is keyed by ``team_id`` /
``session_id`` from day one so the multi-repo future is bought up front.

Kept deliberately small and pure so they are trivial to construct in tests.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- enums expressed as Literals (pure data, no logic) ----------------------

FilesystemLevel = Literal["none", "read", "read-write"]
"""Simple capability preset. Expanded to path globs by ``Capabilities``."""

SessionMode = Literal["parallel", "serial"]
"""LLM execution gateway mode for a session (see runtime/gateway.py)."""

SessionStatus = Literal["active", "paused", "stopped"]

AgentLifecycle = Literal["idle", "running", "waiting-on-agent", "waiting-on-user", "blocked", "done"]
"""The lifecycle states used throughout the system. ``waiting-on-user`` means
an ask_user call is parked on the human's answer."""

TaskStatus = Literal[
    "queued",
    "running",
    "blocked",
    "needs_review",
    "needs_revision",
    "done",
    "failed",
    "cancelled",
]


# --- capabilities -----------------------------------------------------------


class Capabilities(BaseModel):
    """What an agent may touch. The ``filesystem`` level is a one-click preset
    that fills the path globs; ``read_paths``/``write_paths`` are the advanced
    override. Capability is the *same underlying data* as the level — the level
    is just a convenience for the common case.

    Enforcement of these globs lives in the tool layer (agents/capabilities.py /
    agents/tools.py), never in persona prose.
    """

    filesystem: FilesystemLevel = "read-write"
    read_paths: list[str] = Field(default_factory=lambda: ["**"])
    write_paths: list[str] = Field(default_factory=lambda: ["**"])
    bash: bool = True

    @classmethod
    def from_level(cls, level: FilesystemLevel, *, bash: bool = True) -> "Capabilities":
        """Build a profile from just the simple level, filling globs sensibly.

        ``none`` → no paths; ``read`` → read everything, write nothing;
        ``read-write`` → read and write everything. This is the preset the UI's
        non-advanced control produces.
        """
        if level == "none":
            return cls(filesystem="none", read_paths=[], write_paths=[], bash=bash)
        if level == "read":
            return cls(filesystem="read", read_paths=["**"], write_paths=[], bash=bash)
        return cls(filesystem="read-write", read_paths=["**"], write_paths=["**"], bash=bash)

    @property
    def can_read(self) -> bool:
        return self.filesystem in ("read", "read-write") and bool(self.read_paths)

    @property
    def can_write(self) -> bool:
        return self.filesystem == "read-write" and bool(self.write_paths)


# --- agents -----------------------------------------------------------------


class AgentSpec(BaseModel):
    """A node in the team graph: who an agent is and what it may touch.

    ``links`` (who it may delegate to) are derived from graph edges, not stored
    here, so the graph stays the single source of truth for topology.
    """

    id: str
    name: str
    persona: str = ""
    # "<backend-id>:<model-name>" (see providers/registry.py). Default must be
    # a model with LM Studio `tool_use` capability — without it tool calls come
    # back as text and silently do nothing (qwen2.5-coder-* lacks tool_use;
    # see specs/lmstudio-api.md).
    model: str = "lmstudio:qwen/qwen3.5-9b"
    # Thinking preference, applied only when the model's backend supports it
    # (providers/base.ThinkingSupport): None = backend default, True/False =
    # explicit on/off; effort is a backend-specific level (DeepSeek: high|max).
    thinking: bool | None = None
    thinking_effort: str | None = None
    is_entry_point: bool = False
    capabilities: Capabilities = Field(default_factory=Capabilities)


# --- graph ------------------------------------------------------------------


class GraphNode(BaseModel):
    """A positioned agent node. ``position`` is UI-only (React Flow x/y)."""

    spec: AgentSpec
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})


class GraphEdge(BaseModel):
    """A directed delegation permission: ``source`` may ask ``target``.

    ``label`` is the *why* ("React/JSX questions") injected into the source's
    neighbor list so it knows when to consult the target.
    """

    id: str
    source: str
    target: str
    label: str = ""
    # UI-owned routing: signed perpendicular displacement (flow px) of the
    # edge's midpoint, set by dragging the bend handle on the canvas. 0 = auto
    # (straight, or the default arc for reciprocal pairs).
    curve: float = 0.0


class TeamGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def node_ids(self) -> set[str]:
        return {n.spec.id for n in self.nodes}

    def neighbors_of(self, agent_id: str) -> list[GraphEdge]:
        """Outgoing edges from ``agent_id`` — who it may delegate to and why."""
        return [e for e in self.edges if e.source == agent_id]

    def entry_points(self) -> list[str]:
        return [n.spec.id for n in self.nodes if n.spec.is_entry_point]


# --- team (definition / template) -------------------------------------------


class Team(BaseModel):
    """A reusable, repo-agnostic team definition (template)."""

    id: str
    name: str
    graph: TeamGraph = Field(default_factory=TeamGraph)
    created_at: str = ""
    updated_at: str = ""


# --- session (running instance bound to a repo) -----------------------------


class SessionInfo(BaseModel):
    """Serializable description of a running session (the live ``Session``
    object in runtime/sessions.py owns the non-serializable runtime parts)."""

    id: str
    team_id: str
    repo_path: str
    mode: SessionMode = "parallel"
    status: SessionStatus = "active"
    created_at: str = ""


# --- tasks ------------------------------------------------------------------


class Todo(BaseModel):
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


class Task(BaseModel):
    """A first-class unit of work that flows to an entry-point agent and along
    the graph. The status lifecycle and completion signal are detailed in
    runtime/tasks.py; this is the persisted shape."""

    id: str
    session_id: str
    title: str
    prompt: str
    assigned_agent_id: str
    status: TaskStatus = "queued"
    completion_signal: str = "self_reported"
    todos: list[Todo] = Field(default_factory=list)
    parent_task_id: str | None = None
    delegation_chain: list[str] = Field(default_factory=list)
    result: str = ""
    created_at: str = ""
    updated_at: str = ""
