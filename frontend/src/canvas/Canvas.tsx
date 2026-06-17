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
import type { AgentLifecycle } from "../lib/types";

const nodeTypes = { agent: AgentNode };
const edgeTypes = { floating: FloatingEdge };

// Presentational React Flow canvas. All graph state lives in useTeamGraph;
// lifecycle badges come from the SSE event stream. The floating "+" adds an agent.
export default function Canvas(props: {
  nodes: RFNode[];
  edges: Edge[];
  lifecycles: Record<string, AgentLifecycle>;
  waitingOnNames: Record<string, string[]>;
  /** Per-agent teammate IDS it's waiting on (id-keyed, to match edge ids) —
   * drives the sustained edge animation while a delegation is outstanding. */
  waitingOn: Record<string, string[]>;
  onNodesChange: (c: NodeChange<RFNode>[]) => void;
  onEdgesChange: (c: EdgeChange<Edge>[]) => void;
  onConnect: (c: Connection) => void;
  onSelectionChange: (p: OnSelectionChangeParams) => void;
  onUpdateEdgeCurve: (edgeId: string, curve: number) => void;
  addNode: () => void;
  status: string;
  activeEdges: Set<string>;
}) {
  // Inject the live lifecycle + who-it's-waiting-on into each node's data so
  // AgentNode can color its badge and name the blocker(s).
  const nodes = props.nodes.map((n) => ({
    ...n,
    data: { ...n.data, lifecycle: props.lifecycles[n.id] ?? "idle", waitingOnNames: props.waitingOnNames[n.id] },
  }));
  // Render-time edge decoration (the persisted graph stays plain except the
  // user-dragged `curve`): floating edge type + arrowhead; reciprocal pairs
  // flagged so FloatingEdge arcs them apart (derived fresh from the full
  // list, so drawing a reverse edge separates the pair instantly). An edge
  // animates while a delegation is in flight: a SUSTAINED state for the whole
  // wait (source is waiting-on-agent on this target) OR the brief 2.5s pulse
  // from a discrete a2a message — the sustained state is what stays lit until
  // the reply lands.
  const edges = props.edges.map((e) => {
    const reciprocal = props.edges.some((o) => o.source === e.target && o.target === e.source);
    const waiting = (props.waitingOn[e.source] ?? []).includes(e.target);
    const active = waiting || props.activeEdges.has(`${e.source}->${e.target}`);
    return {
      ...e,
      type: "floating",
      data: { ...e.data, reciprocal, onCurveChange: props.onUpdateEdgeCurve },
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
