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
  agentNames,
  onUpdateEdgeLabel,
}: {
  selected: AgentSpec | null;
  onUpdate: (s: AgentSpec) => void;
  events: BusEvent[];
  lifecycles: Record<string, AgentLifecycle>;
  edges: Edge[];
  agentNames: Record<string, string>;
  onUpdateEdgeLabel: (edgeId: string, label: string) => void;
}) {
  const [tab, setTab] = useState<TabKey>("persona");

  return (
    <div
      style={{
        width: 360,
        borderLeft: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        fontFamily: "var(--font)",
        background: "var(--surface)",
        minHeight: 0,
      }}
    >
      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={tab === t.key ? "tab tab-active" : "tab"}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {!selected ? (
          <p className="muted" style={{ padding: 18, fontSize: 13 }}>Select an agent on the canvas to configure it.</p>
        ) : tab === "persona" ? (
          <PersonaTab spec={selected} onUpdate={onUpdate} />
        ) : tab === "capabilities" ? (
          <CapabilitiesTab spec={selected} onUpdate={onUpdate} />
        ) : tab === "agent" ? (
          <AgentTab agentId={selected.id} events={events} lifecycle={lifecycles[selected.id] ?? "idle"} />
        ) : tab === "stats" ? (
          <StatsTab spec={selected} />
        ) : (
          <LinksTab agentId={selected.id} edges={edges} agentNames={agentNames} onUpdateLabel={onUpdateEdgeLabel} />
        )}
      </div>
    </div>
  );
}
