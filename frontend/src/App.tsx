import { useCallback, useEffect, useState } from "react";
import Canvas from "./Canvas";
import Onboarding from "./Onboarding";
import Sidebar from "./Sidebar";
import SessionSwitcher from "./SessionSwitcher";
import TaskBoard from "./TaskBoard";
import { api, setActiveSession, type TeamRow } from "./api";
import { Button } from "./ui";
import { useEvents } from "./useEvents";
import { useTeamGraph } from "./useTeamGraph";
import type { SessionInfo } from "./types";

const LS_KEY = "ag.activeSessionId";

// The control room. Session-centric: you launch a session (a team bound to a
// repo) and operate it. Nothing is auto-created — when there are no sessions,
// the onboarding flow guides you through creating a team and launching one.
export default function App() {
  const [teams, setTeams] = useState<TeamRow[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [activeSessionId, setActiveId] = useState<string | null>(localStorage.getItem(LS_KEY));
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [activeTeamId, setActiveTeamId] = useState<string | null>(null);
  const [view, setView] = useState<"canvas" | "board">("canvas");

  const graph = useTeamGraph(activeTeamId);
  const { events, lifecycles, activeEdges } = useEvents(activeSessionId);

  const selectSession = useCallback((id: string | null) => {
    setActiveSession(id);
    setActiveId(id);
    if (id) localStorage.setItem(LS_KEY, id);
    else localStorage.removeItem(LS_KEY);
  }, []);

  const refresh = useCallback(async () => {
    const [t, s] = await Promise.all([api.listTeams(), api.listSessions()]);
    setTeams(t.teams);
    setSessions(s.sessions);
    // Reconcile the active session against what actually exists.
    setActiveId((cur) => {
      const valid = cur && s.sessions.some((x) => x.id === cur);
      const next = valid ? cur : s.sessions.length ? s.sessions[s.sessions.length - 1].id : null;
      setActiveSession(next);
      if (next) localStorage.setItem(LS_KEY, next);
      else localStorage.removeItem(LS_KEY);
      return next;
    });
  }, []);

  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  // Load the active session's info + point the editor at its team.
  useEffect(() => {
    if (!activeSessionId) {
      setSession(null);
      setActiveTeamId(null);
      return;
    }
    setActiveSession(activeSessionId);
    api
      .session()
      .then((s) => {
        setSession(s);
        setActiveTeamId(s.team_id);
      })
      .catch(() => setSession(null));
  }, [activeSessionId]);

  const saveAs = async () => {
    const name = window.prompt("Save current graph as a new team:");
    if (!name) return;
    await api.createTeam(name, graph.snapshot());
    await refresh();
  };

  const agents = graph.nodes.map((n) => ({
    id: n.data.spec.id,
    name: n.data.spec.name,
    is_entry_point: n.data.spec.is_entry_point,
  }));
  const agentNames = Object.fromEntries(agents.map((a) => [a.id, a.name]));

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      <header
        style={{
          padding: "0 16px",
          height: 52,
          flexShrink: 0,
          borderBottom: "1px solid var(--border)",
          background: "var(--surface)",
          display: "flex",
          alignItems: "center",
          gap: 14,
        }}
      >
        <strong style={{ fontSize: 15, letterSpacing: "-0.02em" }}>
          Agent<span style={{ color: "var(--primary)" }}>Graphs</span>
        </strong>

        {session && (
          <>
            <div style={{ width: 1, height: 22, background: "var(--border)" }} />
            <SessionSwitcher
              activeSessionId={activeSessionId}
              sessions={sessions}
              teams={teams}
              onSwitch={selectSession}
              onLaunched={async (id) => {
                await refresh();
                selectSession(id);
              }}
            />
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
              <Button variant="ghost" size="sm" onClick={saveAs}>
                Save as team…
              </Button>
              <button
                className="chip"
                title="LLM execution gateway — serial runs one model call at a time (low-spec)"
                onClick={() => {
                  const next = session.mode === "serial" ? "parallel" : "serial";
                  api.setMode(next).then(setSession);
                }}
                style={{ cursor: "pointer" }}
              >
                {session.mode}
              </button>
              <div style={{ display: "flex", background: "var(--surface-2)", borderRadius: "var(--r-sm)", padding: 2 }}>
                {(["canvas", "board"] as const).map((v) => (
                  <button
                    key={v}
                    onClick={() => setView(v)}
                    className={view === v ? "btn btn-sm btn-primary" : "btn btn-sm btn-ghost"}
                    style={{ boxShadow: "none" }}
                  >
                    {v === "canvas" ? "Canvas" : "Tasks"}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </header>

      {!activeSessionId ? (
        <Onboarding teams={teams} onChanged={refresh} onLaunched={async (id) => {
          await refresh();
          selectSession(id);
        }} />
      ) : (
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
                onUpdateEdgeCurve={graph.updateEdgeCurve}
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
            agentNames={agentNames}
            onUpdateEdgeLabel={graph.updateEdgeLabel}
            focusEdgeId={graph.selectedEdgeId}
          />
        </div>
      )}
    </div>
  );
}
