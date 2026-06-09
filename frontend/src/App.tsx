import { useEffect, useState } from "react";
import Canvas from "./Canvas";
import Sidebar from "./Sidebar";
import SessionSwitcher from "./SessionSwitcher";
import TaskBoard from "./TaskBoard";
import { api, setActiveSession, type TeamRow } from "./api";
import { useEvents } from "./useEvents";
import { useTeamGraph } from "./useTeamGraph";
import type { SessionInfo } from "./types";

// The control room. Canvas (team graph editor) + five-tab sidebar, with a live
// SSE event stream powering lifecycle badges and the Agent tab.
export default function App() {
  const [activeTeamId, setActiveTeamId] = useState<string | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [teams, setTeams] = useState<TeamRow[]>([]);
  const graph = useTeamGraph(activeTeamId);
  const { events, lifecycles, activeEdges } = useEvents(activeSessionId);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [view, setView] = useState<"canvas" | "board">("canvas");

  const refreshTeams = () => api.listTeams().then((r) => setTeams(r.teams)).catch(() => {});

  // Load the (newly) active session's info, and point its editor at that
  // session's team. setActiveSession() makes module-level api calls target it.
  const loadSession = (id: string | null) => {
    setActiveSession(id);
    api.session().then((s) => {
      setSession(s);
      setActiveSessionId(s.id);
      setActiveTeamId(s.team_id);
    }).catch(() => setSession(null));
  };

  useEffect(() => {
    loadSession(null);
    refreshTeams();
  }, []);

  const saveAs = async () => {
    const name = window.prompt("Save current graph as team:");
    if (!name) return;
    const team = await api.createTeam(name, graph.snapshot());
    await refreshTeams();
    setActiveTeamId(team.id);
  };

  const agents = graph.nodes.map((n) => ({
    id: n.data.spec.id,
    name: n.data.spec.name,
    is_entry_point: n.data.spec.is_entry_point,
  }));

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
        <span style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
          <select
            value={activeTeamId ?? ""}
            onChange={(e) => setActiveTeamId(e.target.value)}
            title="Load a team definition into the editor"
          >
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {session && t.id === session.team_id ? " (session)" : ""}
              </option>
            ))}
          </select>
          <button onClick={saveAs} style={{ fontSize: 11, padding: "2px 8px", cursor: "pointer" }}>
            Save as…
          </button>
        </span>
        <SessionSwitcher
          activeSessionId={activeSessionId}
          teams={teams}
          onSwitch={(id) => loadSession(id)}
          onLaunched={(id) => loadSession(id)}
        />
        {session && (
          <span style={{ fontSize: 12, color: "#6b7280", display: "flex", alignItems: "center", gap: 8 }}>
            session {session.id.slice(0, 12)} · repo {session.repo_path}
            <button
              title="LLM execution gateway: serial = one model call at a time (low-spec)"
              onClick={() => {
                const next = session.mode === "serial" ? "parallel" : "serial";
                api.setMode(next).then(setSession);
              }}
              style={{ fontSize: 11, padding: "2px 8px", border: "1px solid #d1d5db", borderRadius: 6, cursor: "pointer", background: "white" }}
            >
              {session.mode}
            </button>
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          {(["canvas", "board"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              style={{
                padding: "4px 12px",
                border: "1px solid #d1d5db",
                borderRadius: 6,
                background: view === v ? "#2563eb" : "white",
                color: view === v ? "white" : "#374151",
                cursor: "pointer",
                fontSize: 12,
              }}
            >
              {v === "canvas" ? "Canvas" : "Task board"}
            </button>
          ))}
        </div>
      </header>

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {view === "canvas" ? (
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
          ) : (
            <TaskBoard agents={agents} events={events} />
          )}
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
