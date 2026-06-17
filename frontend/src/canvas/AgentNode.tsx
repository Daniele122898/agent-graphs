import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { AgentNodeData } from "./graphMapping";
import type { AgentLifecycle } from "../lib/types";

const LIFECYCLE: Record<AgentLifecycle, { color: string; label: string }> = {
  idle: { color: "#9aa4b2", label: "idle" },
  running: { color: "#15803d", label: "running" },
  "waiting-on-agent": { color: "#b45309", label: "waiting" },
  "waiting-on-user": { color: "#7c3aed", label: "needs you" },
  blocked: { color: "#c2341d", label: "blocked" },
  done: { color: "#2563eb", label: "done" },
};

// Custom React Flow node for an agent — a clean card with a live status dot,
// entry-point marker, and the model it runs on.
export default function AgentNode({ data, selected }: NodeProps) {
  const spec = (data as AgentNodeData).spec;
  const lifecycle: AgentLifecycle = (data as { lifecycle?: AgentLifecycle }).lifecycle ?? "idle";
  const waitingOnNames = (data as { waitingOnNames?: string[] }).waitingOnNames;
  const lc = LIFECYCLE[lifecycle];
  const model = spec.model.split(":").pop();

  return (
    <div
      style={{
        minWidth: 168,
        padding: "11px 14px",
        borderRadius: "var(--r)",
        border: `1.5px solid ${selected ? "var(--primary)" : "var(--border-strong)"}`,
        background: "var(--surface)",
        boxShadow: selected ? "0 0 0 3px var(--primary-ring)" : "var(--shadow-sm)",
        fontFamily: "var(--font)",
        transition: "border-color 0.12s ease, box-shadow 0.12s ease",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: "var(--border-strong)", width: 8, height: 8 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span title={lc.label} style={{ width: 9, height: 9, borderRadius: "50%", background: lc.color, flexShrink: 0, boxShadow: `0 0 0 3px ${lc.color}22` }} />
        <strong style={{ fontSize: 13.5, letterSpacing: "-0.01em" }}>{spec.name}</strong>
        {spec.is_entry_point && (
          <span title="entry point" style={{ marginLeft: "auto", fontSize: 10, color: "var(--primary)", background: "var(--primary-soft)", padding: "1px 6px", borderRadius: "var(--r-full)", fontWeight: 600 }}>
            entry
          </span>
        )}
      </div>
      <div className="mono" style={{ fontSize: 10.5, color: "var(--text-faint)", marginTop: 5 }}>{model}</div>
      {lifecycle === "waiting-on-agent" && waitingOnNames && waitingOnNames.length > 0 && (
        <div style={{ fontSize: 10.5, color: lc.color, marginTop: 4, fontWeight: 600 }}>
          ⏳ waiting on {waitingOnNames.join(", ")}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: "var(--primary)", width: 8, height: 8 }} />
    </div>
  );
}
