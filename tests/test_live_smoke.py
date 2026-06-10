"""Live-LLM smoke tier — OFF by default.

Run only with a real local model loaded in LM Studio:

    AGENT_GRAPHS_LIVE=1 pytest tests/test_live_smoke.py -s

These verify what FunctionModel cannot: that a *real* model actually chooses to
call the tools (the spec's main risk — local-model tool-calling quality). They
are slow and flaky by nature, so they never run in the fast deterministic suite.
"""

from __future__ import annotations

import os

import pytest

from backend.agents import build_agent
from backend.models import resolve_model
from backend.models_domain import AgentSpec, Capabilities
from backend.todos import AgentDeps
from backend.tools import DevTools

LIVE = os.environ.get("AGENT_GRAPHS_LIVE") == "1"
MODEL = os.environ.get("AGENT_GRAPHS_LIVE_MODEL", "lmstudio:qwen/qwen3.5-9b")

pytestmark = pytest.mark.skipif(not LIVE, reason="set AGENT_GRAPHS_LIVE=1 to run live-model smoke tests")


async def test_real_model_writes_a_file(repo):
    spec = AgentSpec(
        id="lead",
        name="Lead",
        persona="You are a coding agent. Use your tools to make the requested change.",
        model=MODEL,
        capabilities=Capabilities.from_level("read-write"),
    )
    agent = build_agent(spec, model=resolve_model(MODEL), dev_tools=DevTools(repo, spec.capabilities))
    await agent.run(
        "Create a file named hello.txt in the repo root containing exactly: hi", deps=AgentDeps()
    )
    assert (repo / "hello.txt").exists(), "the model did not call write_file"
