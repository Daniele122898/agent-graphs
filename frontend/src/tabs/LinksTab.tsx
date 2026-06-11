import { useEffect, useRef } from "react";
import type { Edge } from "@xyflow/react";
import { TextInput } from "../ui";

// Links tab: WHO an agent can talk to. Outgoing edges become the agent's
// ask_agent neighbor list; the label is the *why* injected into its
// instructions ("React/JSX questions"). Draw edges on the canvas; label them
// here — or click an edge on the canvas to land directly on its row.
export default function LinksTab({
  agentId,
  edges,
  agentNames,
  onUpdateLabel,
  focusEdgeId = null,
}: {
  agentId: string;
  edges: Edge[];
  agentNames: Record<string, string>;
  onUpdateLabel: (edgeId: string, label: string) => void;
  focusEdgeId?: string | null;
}) {
  const outgoing = edges.filter((e) => e.source === agentId);
  const incoming = edges.filter((e) => e.target === agentId);

  const nameOf = (id: string) => agentNames[id] ?? id;
  const focusRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (focusEdgeId && focusRef.current) {
      focusRef.current.focus();
      focusRef.current.scrollIntoView({ block: "nearest" });
    }
  }, [focusEdgeId]);

  return (
    <div style={{ padding: 16, fontSize: 13, display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Can consult →</div>
        {outgoing.length === 0 && <div style={{ color: "var(--text-faint)" }}>No outgoing links. Draw edges on the canvas.</div>}
        {outgoing.map((e) => {
          const focused = e.id === focusEdgeId;
          return (
            <div
              key={e.id}
              style={{
                marginBottom: 10,
                padding: focused ? "8px 10px" : 0,
                margin: focused ? "0 -10px 10px" : "0 0 10px",
                borderRadius: "var(--r-sm)",
                background: focused ? "var(--primary-soft)" : "transparent",
              }}
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <span style={{ fontWeight: 600 }}>{nameOf(e.target)}</span>
                <span className="mono" style={{ fontSize: 11, color: "var(--text-faint)" }}>{e.target}</span>
              </div>
              <TextInput
                ref={focused ? focusRef : undefined}
                value={typeof e.label === "string" ? e.label : ""}
                onChange={(ev) => onUpdateLabel(e.id, ev.target.value)}
                placeholder="why — e.g. React/JSX questions"
                style={{ marginTop: 3 }}
              />
            </div>
          );
        })}
      </div>

      <div>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>← Can be consulted by</div>
        {incoming.length === 0 ? (
          <div style={{ color: "var(--text-faint)" }}>No incoming links.</div>
        ) : (
          <ul style={{ paddingLeft: 18, margin: 0, display: "flex", flexDirection: "column", gap: 4 }}>
            {incoming.map((e) => (
              <li key={e.id}>
                <span style={{ fontWeight: 600 }}>{nameOf(e.source)}</span>{" "}
                <span className="mono" style={{ fontSize: 11, color: "var(--text-faint)" }}>{e.source}</span>
                {typeof e.label === "string" && e.label ? <span style={{ color: "var(--text-muted)" }}> — {e.label}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
