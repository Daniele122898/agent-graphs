import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type EdgeChange,
  type NodeChange,
  type Connection,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import AgentNode from "./AgentNode";
import type { RFNode } from "./graphMapping";
import type { AgentLifecycle } from "./types";

const nodeTypes = { agent: AgentNode };

// Presentational React Flow canvas. All graph state lives in useTeamGraph;
// lifecycle badges come from the SSE event stream. The floating "+" adds an agent.
export default function Canvas(props: {
  nodes: RFNode[];
  edges: Edge[];
  lifecycles: Record<string, AgentLifecycle>;
  onNodesChange: (c: NodeChange<RFNode>[]) => void;
  onEdgesChange: (c: EdgeChange<Edge>[]) => void;
  onConnect: (c: Connection) => void;
  onSelectionChange: (p: { nodes: RFNode[] }) => void;
  addNode: () => void;
  status: string;
  activeEdges: Set<string>;
}) {
  // Inject the live lifecycle into each node's data so AgentNode can color its badge.
  const nodes = props.nodes.map((n) => ({
    ...n,
    data: { ...n.data, lifecycle: props.lifecycles[n.id] ?? "idle" },
  }));
  // Animate edges that have a recent delegation message flowing along them.
  const edges = props.edges.map((e) => {
    const active = props.activeEdges.has(`${e.source}->${e.target}`);
    return active
      ? { ...e, animated: true, style: { ...e.style, stroke: "#2563eb", strokeWidth: 2 } }
      : e;
  });
  const statusColor = props.status.includes("error") ? "crimson" : "#6b7280";

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
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
        onClick={props.addNode}
        title="Add agent"
        style={{
          position: "absolute",
          bottom: 12,
          left: 12,
          width: 40,
          height: 40,
          borderRadius: "50%",
          border: "none",
          background: "#2563eb",
          color: "white",
          fontSize: 22,
          cursor: "pointer",
          boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
          zIndex: 5,
        }}
      >
        +
      </button>
      <span
        style={{
          position: "absolute",
          bottom: 20,
          left: 64,
          fontSize: 12,
          color: statusColor,
          fontFamily: "system-ui, sans-serif",
        }}
      >
        {props.status}
      </span>
    </div>
  );
}
