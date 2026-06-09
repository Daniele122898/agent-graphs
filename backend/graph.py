"""Graph validation — pure functions over a ``TeamGraph``.

The graph *is* the org chart: nodes are agents, directed edges are delegation
permissions (source may ask target). These checks are pure (data in, errors
out) so they are trivial to test and reused by both the save endpoint and the
session-launch readiness check.

Two tiers, deliberately separated:

- **Structural** validation is always enforced on save: ids unique, edges
  reference existing nodes, no self-loops, no duplicate edges. A malformed graph
  can corrupt the runtime, so it must never persist.
- **Runnability** (>=1 entry point) is only required to *launch a session*, not
  to *save a draft* — the editor must let you build a graph incrementally.
"""

from __future__ import annotations

from .models_domain import TeamGraph


def validate_structure(graph: TeamGraph) -> list[str]:
    """Return a list of structural errors (empty = structurally valid)."""
    errors: list[str] = []

    ids = [n.spec.id for n in graph.nodes]
    seen: set[str] = set()
    dupes: set[str] = set()
    for i in ids:
        if i in seen:
            dupes.add(i)
        seen.add(i)
    if dupes:
        errors.append(f"duplicate node ids: {sorted(dupes)}")

    node_ids = set(ids)
    edge_keys: set[tuple[str, str]] = set()
    for e in graph.edges:
        if e.source not in node_ids:
            errors.append(f"edge {e.id}: source '{e.source}' is not a node")
        if e.target not in node_ids:
            errors.append(f"edge {e.id}: target '{e.target}' is not a node")
        if e.source == e.target:
            errors.append(f"edge {e.id}: self-loop on '{e.source}'")
        key = (e.source, e.target)
        if key in edge_keys:
            errors.append(f"duplicate edge {e.source} -> {e.target}")
        edge_keys.add(key)

    return errors


def validate_runnable(graph: TeamGraph) -> list[str]:
    """Errors that block *launching* a session (structure + readiness)."""
    errors = validate_structure(graph)
    if not graph.nodes:
        errors.append("graph has no agents")
    elif not graph.entry_points():
        errors.append("graph has no entry-point agent (>=1 required to run)")
    return errors
