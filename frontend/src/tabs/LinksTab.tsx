import type { Edge } from "@xyflow/react";
import { TextInput } from "../ui";

// Links tab: WHO an agent can talk to. Outgoing edges become the agent's
// ask_agent neighbor list; the label is the *why* injected into its
// instructions ("React/JSX questions"). Draw edges on the canvas; label them here.
export default function LinksTab({
  agentId,
  edges,
  agentNames,
  onUpdateLabel,
}: {
  agentId: string;
  edges: Edge[];
  agentNames: Record<string, string>;
  onUpdateLabel: (edgeId: string, label: string) => void;
}) {
  const outgoing = edges.filter((e) => e.source === agentId);
  const incoming = edges.filter((e) => e.target === agentId);

  const nameOf = (id: string) => agentNames[id] ?? id;

  return (
    <div style={{ padding: 16, fontSize: 13, display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Can consult →</div>
        {outgoing.length === 0 && <div style={{ color: "var(--text-faint)" }}>No outgoing links. Draw edges on the canvas.</div>}
        {outgoing.map((e) => (
          <div key={e.id} style={{ marginBottom: 10 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
              <span style={{ fontWeight: 600 }}>{nameOf(e.target)}</span>
              <span className="mono" style={{ fontSize: 11, color: "var(--text-faint)" }}>{e.target}</span>
            </div>
            <TextInput
              value={typeof e.label === "string" ? e.label : ""}
              onChange={(ev) => onUpdateLabel(e.id, ev.target.value)}
              placeholder="why — e.g. React/JSX questions"
              style={{ marginTop: 3 }}
            />
          </div>
        ))}
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
