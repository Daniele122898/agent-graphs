"""Graph validation + round-trip tests.

Failures here mean something is broken (a malformed graph would persist, or a
valid one would be rejected, or data would be lost through serialization) —
never merely "changed".
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.graph import validate_runnable, validate_structure
from backend.main import create_app
from backend.models_domain import AgentSpec, GraphEdge, GraphNode, TeamGraph


def _node(id_: str, entry: bool = False) -> GraphNode:
    return GraphNode(spec=AgentSpec(id=id_, name=id_.title(), is_entry_point=entry))


def test_valid_graph_has_no_structural_errors():
    g = TeamGraph(
        nodes=[_node("a", entry=True), _node("b")],
        edges=[GraphEdge(id="e1", source="a", target="b", label="ask b")],
    )
    assert validate_structure(g) == []


def test_edge_to_missing_node_is_rejected():
    g = TeamGraph(nodes=[_node("a")], edges=[GraphEdge(id="e1", source="a", target="ghost")])
    errors = validate_structure(g)
    assert any("ghost" in e for e in errors)


def test_self_loop_rejected():
    g = TeamGraph(nodes=[_node("a")], edges=[GraphEdge(id="e1", source="a", target="a")])
    assert any("self-loop" in e for e in validate_structure(g))


def test_duplicate_node_ids_rejected():
    g = TeamGraph(nodes=[_node("a"), _node("a")])
    assert any("duplicate node ids" in e for e in validate_structure(g))


def test_duplicate_edge_rejected():
    g = TeamGraph(
        nodes=[_node("a"), _node("b")],
        edges=[
            GraphEdge(id="e1", source="a", target="b"),
            GraphEdge(id="e2", source="a", target="b"),
        ],
    )
    assert any("duplicate edge" in e for e in validate_structure(g))


def test_runnable_requires_entry_point():
    g = TeamGraph(nodes=[_node("a")])  # no entry point
    assert any("entry-point" in e for e in validate_runnable(g))
    g2 = TeamGraph(nodes=[_node("a", entry=True)])
    assert validate_runnable(g2) == []


def test_graph_round_trips_through_api(tmp_path):
    app = create_app(db_path=tmp_path / "g.sqlite", repo_path=tmp_path / "repo")
    with TestClient(app) as client:
        new_graph = TeamGraph(
            nodes=[
                _node("lead", entry=True),
                GraphNode(spec=AgentSpec(id="react", name="React Expert"), position={"x": 200, "y": 100}),
            ],
            edges=[GraphEdge(id="e1", source="lead", target="react", label="React/JSX questions")],
        )
        put = client.put("/api/team/graph", json=new_graph.model_dump())
        assert put.status_code == 200

        got = client.get("/api/team/graph").json()
        assert {n["spec"]["id"] for n in got["nodes"]} == {"lead", "react"}
        assert got["edges"][0]["label"] == "React/JSX questions"
        # position survives the round-trip
        react = next(n for n in got["nodes"] if n["spec"]["id"] == "react")
        assert react["position"] == {"x": 200, "y": 100}


def test_api_rejects_malformed_graph(tmp_path):
    app = create_app(db_path=tmp_path / "g.sqlite", repo_path=tmp_path / "repo")
    with TestClient(app) as client:
        bad = TeamGraph(
            nodes=[_node("a")],
            edges=[GraphEdge(id="e1", source="a", target="missing")],
        )
        r = client.put("/api/team/graph", json=bad.model_dump())
        assert r.status_code == 422
