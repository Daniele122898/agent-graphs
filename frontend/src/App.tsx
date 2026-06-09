import { useEffect, useState } from "react";
import Canvas from "./Canvas";
import Sidebar from "./Sidebar";
import { api } from "./api";
import { useEvents } from "./useEvents";
import { useTeamGraph } from "./useTeamGraph";
import type { SessionInfo } from "./types";

// The control room. Canvas (team graph editor) + five-tab sidebar, with a live
// SSE event stream powering lifecycle badges and the Agent tab.
export default function App() {
  const graph = useTeamGraph();
  const { events, lifecycles, activeEdges } = useEvents();
  const [session, setSession] = useState<SessionInfo | null>(null);

  useEffect(() => {
    api.session().then(setSession).catch(() => setSession(null));
  }, []);

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <header
        style={{
          padding: "8px 16px",
          borderBottom: "1px solid #e5e7eb",
          display: "flex",
          alignItems: "baseline",
          gap: 16,
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <strong>Agent Graphs</strong>
        {session && (
          <span style={{ fontSize: 12, color: "#6b7280" }}>
            session {session.id.slice(0, 12)} · repo {session.repo_path} · {session.mode}
          </span>
        )}
      </header>

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Canvas
            nodes={graph.nodes}
            edges={graph.edges}
            lifecycles={lifecycles}
            onNodesChange={graph.onNodesChange}
            onEdgesChange={graph.onEdgesChange}
            onConnect={graph.onConnect}
            onSelectionChange={graph.onSelectionChange}
            addNode={graph.addNode}
            status={graph.status}
            activeEdges={activeEdges}
          />
        </div>
        <Sidebar
          selected={graph.selectedSpec}
          onUpdate={graph.updateSpec}
          events={events}
          lifecycles={lifecycles}
          edges={graph.edges}
          onUpdateEdgeLabel={graph.updateEdgeLabel}
        />
      </div>
    </div>
  );
}
