import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type EdgeChange,
  type NodeChange,
  type Connection,
  type OnSelectionChangeParams,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import AgentNode from "./AgentNode";
import FloatingEdge from "./FloatingEdge";
import type { RFNode } from "./graphMapping";
import type { AgentLifecycle } from "./types";

const nodeTypes = { agent: AgentNode };
const edgeTypes = { floating: FloatingEdge };

// Presentational React Flow canvas. All graph state lives in useTeamGraph;
// lifecycle badges come from the SSE event stream. The floating "+" adds an agent.
export default function Canvas(props: {
  nodes: RFNode[];
  edges: Edge[];
  lifecycles: Record<string, AgentLifecycle>;
  onNodesChange: (c: NodeChange<RFNode>[]) => void;
  onEdgesChange: (c: EdgeChange<Edge>[]) => void;
  onConnect: (c: Connection) => void;
  onSelectionChange: (p: OnSelectionChangeParams) => void;
  addNode: () => void;
  status: string;
  activeEdges: Set<string>;
}) {
  // Inject the live lifecycle into each node's data so AgentNode can color its badge.
  const nodes = props.nodes.map((n) => ({
    ...n,
    data: { ...n.data, lifecycle: props.lifecycles[n.id] ?? "idle" },
  }));
  // Render-time edge decoration (the persisted graph stays plain): floating
  // edge type + arrowhead; reciprocal pairs get opposite perpendicular offsets
  // so A⇄B renders as two parallel arcs (derived fresh from the full list, so
  // drawing a reverse edge separates the pair instantly); recently-active
  // delegation edges animate.
  const edges = props.edges.map((e) => {
    const reciprocal = props.edges.some((o) => o.source === e.target && o.target === e.source);
    const offsetDir = reciprocal ? (e.source < e.target ? 1 : -1) : 0;
    const active = props.activeEdges.has(`${e.source}->${e.target}`);
    return {
      ...e,
      type: "floating",
      data: { ...e.data, offsetDir },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: active ? "#2563eb" : "#b1b1b7" },
      ...(active
        ? { animated: true, style: { ...e.style, stroke: "#2563eb", strokeWidth: 2 } }
        : {}),
    };
  });

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={props.onNodesChange}
        onEdgesChange={props.onEdgesChange}
        onConnect={props.onConnect}
        onSelectionChange={props.onSelectionChange}
        fitView
      >
        <Background />
        {/* Zoom controls moved to top-left so they don't collide with the
            bottom-left "add agent" button. */}
        <Controls position="top-left" />
      </ReactFlow>

      <button
        className="fab"
        onClick={props.addNode}
        title="Add agent"
        style={{ position: "absolute", bottom: 16, left: 16, zIndex: 5 }}
      >
        +
      </button>
      <span
        className={props.status.includes("error") ? "chip chip-danger" : "chip"}
        style={{ position: "absolute", bottom: 24, left: 72, zIndex: 5 }}
      >
        {props.status}
      </span>
    </div>
  );
}
