"""Build a Pydantic AI ``Agent`` for a graph node from its spec.

The model is **injected** (never constructed here) so tests pass a
``FunctionModel``. The toolset is generated from the agent's capability profile
(``capabilities.py``), so the agent only ever sees tools it's allowed to use.
The ``write_todos`` tool is always available (progress tracking is universal).
Persona goes in sticky ``instructions``.
"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model

from .a2a import ask_agent, neighbor_instructions
from .capabilities import make_dev_toolset
from .history import compaction_capability
from .models_domain import AgentSpec
from .persona import build_instructions, environment_instructions
from .questions import ask_user
from .todos import AgentDeps, write_todos
from .tools import DevTools


def build_agent(spec: AgentSpec, *, model: Model, dev_tools: DevTools) -> Agent[AgentDeps]:
    """Wire a spec + injected model + capability-derived toolset into an Agent.

    ``dev_tools`` must be bound to the session's repo root, this spec's
    capabilities, and the session's write-lock (the caller owns those). The
    agent also gets ``ask_agent`` (delegation) and a *dynamic* neighbor list
    appended to its instructions, re-evaluated each run so it tracks graph edits.
    """
    agent: Agent[AgentDeps] = Agent(
        model=model,
        deps_type=AgentDeps,
        instructions=build_instructions(spec),
        toolsets=[make_dev_toolset(dev_tools)],
        tools=[write_todos, ask_agent, ask_user],
        capabilities=[compaction_capability()],
        # Small local models need a few self-correction rounds (stale edit
        # hash → re-read → retry); the default of 1 kills runs too eagerly.
        retries=3,
    )

    @agent.instructions
    def _neighbors(ctx: RunContext[AgentDeps]) -> str:
        delegator = ctx.deps.delegator
        if delegator is None:
            return ""
        return neighbor_instructions(delegator.session.graph, ctx.deps.agent_id)

    # Registered last so the freshest, most run-specific facts (cwd, OS, date)
    # sit at the end of the instructions — see specs/pi-harness-learnings.md.
    @agent.instructions
    def _environment(ctx: RunContext[AgentDeps]) -> str:
        return environment_instructions(spec, dev_tools.root)

    return agent
