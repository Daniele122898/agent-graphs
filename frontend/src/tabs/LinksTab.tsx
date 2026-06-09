import type { Edge } from "@xyflow/react";

// Links tab: WHO an agent can talk to. Outgoing edges become the agent's
// ask_agent neighbor list; the label is the *why* injected into its
// instructions ("React/JSX questions"). Draw edges on the canvas; label them here.
export default function LinksTab({
  agentId,
  edges,
  onUpdateLabel,
}: {
  agentId: string;
  edges: Edge[];
  onUpdateLabel: (edgeId: string, label: string) => void;
}) {
  const outgoing = edges.filter((e) => e.source === agentId);
  const incoming = edges.filter((e) => e.target === agentId);

  return (
    <div style={{ padding: 16, fontSize: 13, display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Can consult →</div>
        {outgoing.length === 0 && <div style={{ color: "#9ca3af" }}>No outgoing links. Draw edges on the canvas.</div>}
        {outgoing.map((e) => (
          <div key={e.id} style={{ marginBottom: 8 }}>
            <div style={{ fontFamily: "monospace", fontSize: 12 }}>{e.target}</div>
            <input
              value={typeof e.label === "string" ? e.label : ""}
              onChange={(ev) => onUpdateLabel(e.id, ev.target.value)}
              placeholder="why — e.g. React/JSX questions"
              style={{ width: "100%", padding: 5, marginTop: 2 }}
            />
          </div>
        ))}
      </div>

      <div>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>← Can be consulted by</div>
        {incoming.length === 0 ? (
          <div style={{ color: "#9ca3af" }}>No incoming links.</div>
        ) : (
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            {incoming.map((e) => (
              <li key={e.id} style={{ fontFamily: "monospace", fontSize: 12 }}>
                {e.source}
                {typeof e.label === "string" && e.label ? ` — ${e.label}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
