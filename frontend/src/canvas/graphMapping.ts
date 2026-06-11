// Convert between the backend TeamGraph format and React Flow's node/edge
// shapes. The backend is the source of truth for what an agent *is* (the
// AgentSpec lives in node.data.spec); React Flow owns layout (node.position)
// and interaction. Keeping this mapping in one place means the rest of the UI
// never has to think about two formats.

import type { Edge, Node } from "@xyflow/react";
import type { AgentSpec, GraphEdge, GraphNode, TeamGraph } from "../lib/types";

export type AgentNodeData = { spec: AgentSpec };
export type RFNode = Node<AgentNodeData, "agent">;

export function toReactFlow(graph: TeamGraph): { nodes: RFNode[]; edges: Edge[] } {
  const nodes: RFNode[] = graph.nodes.map((n) => ({
    id: n.spec.id,
    type: "agent",
    position: n.position,
    data: { spec: n.spec },
  }));
  const edges: Edge[] = graph.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.label,
    data: { curve: e.curve ?? 0 },
  }));
  return { nodes, edges };
}

export function fromReactFlow(nodes: RFNode[], edges: Edge[]): TeamGraph {
  const gNodes: GraphNode[] = nodes.map((n) => ({
    spec: n.data.spec,
    position: { x: n.position.x, y: n.position.y },
  }));
  const gEdges: GraphEdge[] = edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: typeof e.label === "string" ? e.label : "",
    curve: typeof e.data?.curve === "number" ? e.data.curve : 0,
  }));
  return { nodes: gNodes, edges: gEdges };
}
