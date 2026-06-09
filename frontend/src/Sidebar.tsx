import { useState } from "react";
import type { Edge } from "@xyflow/react";
import AgentTab from "./tabs/AgentTab";
import CapabilitiesTab from "./tabs/CapabilitiesTab";
import LinksTab from "./tabs/LinksTab";
import PersonaTab from "./tabs/PersonaTab";
import StatsTab from "./tabs/StatsTab";
import type { BusEvent } from "./useEvents";
import type { AgentLifecycle, AgentSpec } from "./types";

// The five-tab sidebar. Each tab owns one question; configure-vs-observe is
// cleanly split: Persona/Capabilities/Links = write, Agent/Stats = observe.
type TabKey = "persona" | "capabilities" | "links" | "agent" | "stats";

const TABS: { key: TabKey; label: string }[] = [
  { key: "persona", label: "Persona" },
  { key: "capabilities", label: "Capabilities" },
  { key: "links", label: "Links" },
  { key: "agent", label: "Agent" },
  { key: "stats", label: "Stats" },
];

export default function Sidebar({
  selected,
  onUpdate,
  events,
  lifecycles,
  edges,
  onUpdateEdgeLabel,
}: {
  selected: AgentSpec | null;
  onUpdate: (s: AgentSpec) => void;
  events: BusEvent[];
  lifecycles: Record<string, AgentLifecycle>;
  edges: Edge[];
  onUpdateEdgeLabel: (edgeId: string, label: string) => void;
}) {
  const [tab, setTab] = useState<TabKey>("persona");

  return (
    <div
      style={{
        width: 340,
        borderLeft: "1px solid #e5e7eb",
        display: "flex",
        flexDirection: "column",
        fontFamily: "system-ui, sans-serif",
        background: "#fafafa",
        minHeight: 0,
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

      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {!selected ? (
          <p style={{ padding: 16, color: "#9ca3af" }}>Select an agent on the canvas.</p>
        ) : tab === "persona" ? (
          <PersonaTab spec={selected} onUpdate={onUpdate} />
        ) : tab === "capabilities" ? (
          <CapabilitiesTab spec={selected} onUpdate={onUpdate} />
        ) : tab === "agent" ? (
          <AgentTab agentId={selected.id} events={events} lifecycle={lifecycles[selected.id] ?? "idle"} />
        ) : tab === "stats" ? (
          <StatsTab spec={selected} />
        ) : (
          <LinksTab agentId={selected.id} edges={edges} onUpdateLabel={onUpdateEdgeLabel} />
        )}
      </div>
    </div>
  );
}
