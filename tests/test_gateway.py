"""LLM execution gateway: serial admits one at a time; parallel doesn't
serialize; per-session isolation. Tested by observed ordering, not sleeps-as-
assertions (we assert non-interleaving, which is the actual contract)."""

from __future__ import annotations

import asyncio

import pytest

from backend.runtime.gateway import GatedModel, Gateway
from backend.domain.models import AgentSpec, Capabilities
from backend.agents.factory import build_agent
from backend.agents.todos import AgentDeps
from backend.agents.tools import DevTools
from pydantic_ai.messages import TextPart
from tests.conftest import make_sequence_model


async def _ordered_runs(gw: Gateway) -> list[str]:
    order: list[str] = []

    async def body(n: int):
        order.append(f"{n}-start")
        await asyncio.sleep(0.02)
        order.append(f"{n}-end")

    async def run(n: int):
        await gw.run(body(n))

    await asyncio.gather(run(1), run(2))
    return order


async def test_serial_gateway_does_not_interleave():
    order = await _ordered_runs(Gateway("serial"))
    # one call fully completes before the other starts
    assert order in (
        ["1-start", "1-end", "2-start", "2-end"],
        ["2-start", "2-end", "1-start", "1-end"],
    )


async def test_parallel_gateway_interleaves():
    order = await _ordered_runs(Gateway("parallel"))
    # both start before either ends
    assert order[:2] == ["1-start", "2-start"] or order[:2] == ["2-start", "1-start"]


async def test_on_wait_fires_when_serial_slot_busy():
    waits = {"n": 0}
    gw = Gateway("serial", on_wait=lambda: waits.__setitem__("n", waits["n"] + 1))
    await _ordered_runs(gw)
    assert waits["n"] >= 1  # the second caller had to wait


def test_set_mode_toggles():
    gw = Gateway("parallel")
    assert gw.mode == "parallel"
    gw.set_mode("serial")
    assert gw.mode == "serial"


async def test_gated_model_still_runs_agent(repo):
    """A GatedModel wrapping a (test) model is transparent to a normal run."""
    model = make_sequence_model([[TextPart("ok")]])
    gated = GatedModel(model, Gateway("serial"))
    agent = build_agent(
        AgentSpec(id="a", name="A", capabilities=Capabilities.from_level("read")),
        model=gated,
        dev_tools=DevTools(repo, Capabilities.from_level("read")),
    )
    result = await agent.run("hello", deps=AgentDeps())
    assert result.output == "ok"


async def test_two_sessions_have_independent_gateways(conn, fake_clock, repo):
    from backend.domain.models import GraphNode, TeamGraph
    from backend.runtime.sessions import SessionManager
    from backend.storage.teams import TeamStore

    team = TeamStore(conn, clock=fake_clock).create(
        "T", TeamGraph(nodes=[GraphNode(spec=AgentSpec(id="a", name="A", is_entry_point=True))])
    )
    mgr = SessionManager(conn, clock=fake_clock)
    s1 = mgr.create_session(team_id=team.id, repo_path=repo / "a", graph=team.graph)
    s2 = mgr.create_session(team_id=team.id, repo_path=repo / "b", graph=team.graph)
    s1.gateway.set_mode("serial")
    assert s1.gateway.mode == "serial"
    assert s2.gateway.mode == "parallel"  # independent
