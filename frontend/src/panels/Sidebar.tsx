import { useCallback, useEffect, useState } from "react";
import type { Edge } from "@xyflow/react";
import AgentTab from "./tabs/AgentTab";
import CapabilitiesTab from "./tabs/CapabilitiesTab";
import LinksTab from "./tabs/LinksTab";
import PersonaTab from "./tabs/PersonaTab";
import StatsTab from "./tabs/StatsTab";
import type { BusEvent } from "../hooks/useEvents";
import type { AgentLifecycle, AgentSpec } from "../lib/types";

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

// Resizable width, persisted across sessions. The content (chat bubbles use
// percentage widths, tabs flex) adapts to whatever width the user drags to.
const LS_WIDTH = "ag.sidebarWidth";
const MIN_W = 300;
const MAX_W = 820;

function initialWidth(): number {
  const v = Number(localStorage.getItem(LS_WIDTH));
  return Number.isFinite(v) && v >= MIN_W && v <= MAX_W ? v : 360;
}

export default function Sidebar({
  selected,
  onUpdate,
  events,
  lifecycles,
  edges,
  agentNames,
  onUpdateEdgeLabel,
  focusEdgeId,
}: {
  selected: AgentSpec | null;
  onUpdate: (s: AgentSpec) => void;
  events: BusEvent[];
  lifecycles: Record<string, AgentLifecycle>;
  edges: Edge[];
  agentNames: Record<string, string>;
  onUpdateEdgeLabel: (edgeId: string, label: string) => void;
  focusEdgeId: string | null;
}) {
  const [tab, setTab] = useState<TabKey>("persona");
  const [width, setWidth] = useState(initialWidth);

  // Clicking an edge on the canvas selects its source agent AND jumps the
  // sidebar to the Links tab so the link is immediately editable.
  useEffect(() => {
    if (focusEdgeId) setTab("links");
  }, [focusEdgeId]);

  const startResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const onMove = (ev: PointerEvent) => {
      const w = Math.min(MAX_W, Math.max(MIN_W, window.innerWidth - ev.clientX));
      setWidth(w);
      localStorage.setItem(LS_WIDTH, String(w));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, []);

  return (
    <div
      style={{
        width,
        position: "relative",
        flexShrink: 0,
        borderLeft: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        fontFamily: "var(--font)",
        background: "var(--surface)",
        minHeight: 0,
      }}
    >
      <div
        onPointerDown={startResize}
        title="Drag to resize"
        style={{
          position: "absolute",
          left: -4,
          top: 0,
          bottom: 0,
          width: 8,
          cursor: "col-resize",
          zIndex: 10,
        }}
      />
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
          <LinksTab agentId={selected.id} edges={edges} agentNames={agentNames} onUpdateLabel={onUpdateEdgeLabel} focusEdgeId={focusEdgeId} />
        )}
      </div>
    </div>
  );
}
