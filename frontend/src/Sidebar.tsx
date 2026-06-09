import { useState } from "react";
import type { AgentSpec } from "./types";

// The five-tab sidebar. Each tab owns one question; configure-vs-observe is
// cleanly split (Persona/Capabilities/Links = write, Agent/Stats = observe).
// Phase 1 ships shells that show the selected agent; the tabs gain real
// behavior in later phases (Capabilities/Persona editing → Phase 2,
// Links → Phase 4, Agent stream → Phase 2/3, Stats → Phase 2).

type TabKey = "persona" | "capabilities" | "links" | "agent" | "stats";

const TABS: { key: TabKey; label: string; phase: string }[] = [
  { key: "persona", label: "Persona", phase: "Phase 2" },
  { key: "capabilities", label: "Capabilities", phase: "Phase 2" },
  { key: "links", label: "Links", phase: "Phase 4" },
  { key: "agent", label: "Agent", phase: "Phase 2/3" },
  { key: "stats", label: "Stats", phase: "Phase 2" },
];

function Shell({ title, phase, children }: { title: string; phase: string; children?: React.ReactNode }) {
  return (
    <div style={{ padding: 16 }}>
      <h3 style={{ margin: "0 0 8px" }}>{title}</h3>
      {children}
      <p style={{ color: "#9ca3af", fontSize: 12, marginTop: 12 }}>
        Editing arrives in {phase}.
      </p>
    </div>
  );
}

export default function Sidebar({ selected }: { selected: AgentSpec | null }) {
  const [tab, setTab] = useState<TabKey>("persona");

  return (
    <div
      style={{
        width: 320,
        borderLeft: "1px solid #e5e7eb",
        display: "flex",
        flexDirection: "column",
        fontFamily: "system-ui, sans-serif",
        background: "#fafafa",
      }}
    >
      <div style={{ display: "flex", borderBottom: "1px solid #e5e7eb" }}>
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              flex: 1,
              padding: "10px 4px",
              border: "none",
              borderBottom: tab === t.key ? "2px solid #2563eb" : "2px solid transparent",
              background: "transparent",
              cursor: "pointer",
              fontSize: 12,
              fontWeight: tab === t.key ? 600 : 400,
              color: tab === t.key ? "#2563eb" : "#374151",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>
        {!selected ? (
          <p style={{ padding: 16, color: "#9ca3af" }}>Select an agent on the canvas.</p>
        ) : (
          <>
            {tab === "persona" && (
              <Shell title="Persona" phase="Phase 2">
                <p style={{ fontSize: 13 }}>
                  <strong>{selected.name}</strong>
                </p>
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    fontSize: 12,
                    background: "white",
                    border: "1px solid #e5e7eb",
                    borderRadius: 6,
                    padding: 8,
                  }}
                >
                  {selected.persona || "(no persona yet)"}
                </pre>
              </Shell>
            )}
            {tab === "capabilities" && (
              <Shell title="Capabilities" phase="Phase 2">
                <ul style={{ fontSize: 13, paddingLeft: 18 }}>
                  <li>filesystem: {selected.capabilities.filesystem}</li>
                  <li>read: {selected.capabilities.read_paths.join(", ") || "—"}</li>
                  <li>write: {selected.capabilities.write_paths.join(", ") || "—"}</li>
                  <li>bash: {selected.capabilities.bash ? "on" : "off"}</li>
                  <li>model: {selected.model}</li>
                </ul>
              </Shell>
            )}
            {tab === "links" && <Shell title="Links" phase="Phase 4" />}
            {tab === "agent" && <Shell title="Agent" phase="Phase 2/3" />}
            {tab === "stats" && <Shell title="Stats" phase="Phase 2" />}
          </>
        )}
      </div>
    </div>
  );
}
