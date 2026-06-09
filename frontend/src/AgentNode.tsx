import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { AgentNodeData } from "./graphMapping";
import type { AgentLifecycle } from "./types";

const LIFECYCLE_COLOR: Record<AgentLifecycle, string> = {
  idle: "#9ca3af",
  running: "#22c55e",
  "waiting-on-agent": "#f59e0b",
  blocked: "#ef4444",
  done: "#3b82f6",
};

// Custom React Flow node for an agent. Source/target handles let edges be drawn
// by dragging. The lifecycle badge is a placeholder (idle) in Phase 1; Phase 3
// wires it to live status from the session registry.
export default function AgentNode({ data, selected }: NodeProps) {
  const spec = (data as AgentNodeData).spec;
  const lifecycle: AgentLifecycle = "idle";
  return (
    <div
      style={{
        minWidth: 150,
        padding: "10px 14px",
        borderRadius: 10,
        border: `2px solid ${selected ? "#2563eb" : "#d1d5db"}`,
        background: "white",
        boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          title={lifecycle}
          style={{
            width: 10,
            height: 10,
            borderRadius: "50%",
            background: LIFECYCLE_COLOR[lifecycle],
            flexShrink: 0,
          }}
        />
        <strong style={{ fontSize: 14 }}>{spec.name}</strong>
      </div>
      <div style={{ fontSize: 11, color: "#6b7280", marginTop: 4 }}>
        {spec.is_entry_point && <span title="entry point">⭐ </span>}
        {spec.model.split(":").pop()}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
