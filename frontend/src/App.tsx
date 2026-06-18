import { useCallback, useEffect, useState } from "react";
import Canvas from "./canvas/Canvas";
import Onboarding from "./panels/Onboarding";
import Sidebar from "./panels/Sidebar";
import SessionSwitcher from "./panels/SessionSwitcher";
import TaskBoard from "./panels/TaskBoard";
import { api, setActiveSession, withRetry, type TeamRow } from "./lib/api";
import { Button, IconButton, Select } from "./lib/ui";
import { useEvents } from "./hooks/useEvents";
import { useTeamGraph } from "./hooks/useTeamGraph";
import type { SessionInfo } from "./lib/types";

const LS_KEY = "ag.activeSessionId";

// Compact, fixed-size autosave indicator (a state dot + one word) for the Team
// zone — replaces the old "saved — {long team name}" chip that wrapped to two
// lines. The team it refers to is the adjacent selector.
function SaveDot({ status }: { status: string }) {
  const error = status.includes("error");
  const busy = status.startsWith("saving") || status.startsWith("loading");
  const cls = error ? "is-error" : busy ? "is-saving" : "is-saved";
  const label = error ? "Save failed" : status.startsWith("loading") ? "Loading…" : busy ? "Saving…" : "Saved";
  return (
    <span className={`savedot ${cls}`} title={error ? status : "Canvas + agent edits auto-save to this team"}>
      <span className="dot" />
      {label}
    </span>
  );
}

// Two overlapping rounded squares — the universal "copy / duplicate" glyph.
function CopyIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="5.5" y="5.5" width="8" height="8" rx="2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M10.5 5.5V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h1.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

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
  const { events, lifecycles, waitingOn, activeEdges } = useEvents(activeSessionId);

  const selectSession = useCallback((id: string | null) => {
    setActiveSession(id);
    setActiveId(id);
    if (id) localStorage.setItem(LS_KEY, id);
    else localStorage.removeItem(LS_KEY);
  }, []);

  const refresh = useCallback(async () => {
    const [t, s] = await withRetry(() => Promise.all([api.listTeams(), api.listSessions()]));
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

  // Load the active session's info + point the editor at its team. Retried:
  // a one-shot failure here (e.g. the backend mid --reload restart) used to
  // leave activeTeamId null forever — empty canvas until a manual refresh.
  useEffect(() => {
    if (!activeSessionId) {
      setSession(null);
      setActiveTeamId(null);
      return;
    }
    setActiveSession(activeSessionId);
    let cancelled = false;
    withRetry(() => api.session())
      .then((s) => {
        if (cancelled) return;
        setSession(s);
        setActiveTeamId(s.team_id);
      })
      .catch(() => {
        if (!cancelled) setSession(null);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  // Fork the current graph into a NEW team (a copy) and point the editor at it.
  // Pure fork: it does NOT switch the running session onto the copy — that's an
  // explicit, separate action ("↻ Use for session"), so "save a copy" can't
  // surprise you by detaching the session you're running. Switching the editor
  // (setActiveTeamId) triggers the hook's flush-on-switch, persisting the
  // ORIGINAL team's pending edits before the copy loads.
  const saveAs = async () => {
    const name = window.prompt("Save the current graph as a NEW team (a copy):");
    if (!name) return;
    const t = await api.createTeam(name, graph.snapshot());
    setActiveTeamId(t.id);
    await refresh();
  };

  const agents = graph.nodes.map((n) => ({
    id: n.data.spec.id,
    name: n.data.spec.name,
    is_entry_point: n.data.spec.is_entry_point,
  }));
  const agentNames = Object.fromEntries(agents.map((a) => [a.id, a.name]));
  // Map each waiting agent's blocker IDs to display names for the UI.
  const waitingOnNames: Record<string, string[]> = Object.fromEntries(
    Object.entries(waitingOn).map(([id, targets]) => [id, targets.map((t) => agentNames[t] ?? t)])
  );
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
            <div className="hdiv" />
            {/* TEAM zone — what the canvas is EDITING (its graph auto-saves here) */}
            <div className="hzone">
              <span className="hcap">Team</span>
              <Select
                value={activeTeamId ?? ""}
                onChange={(e) => {
                  const newTeamId = e.target.value;
                  if (newTeamId && newTeamId !== activeTeamId) setActiveTeamId(newTeamId);
                }}
                title="The team graph you're editing on the canvas"
                style={{ width: "auto", minWidth: 150, maxWidth: 220 }}
              >
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </Select>
              <SaveDot status={graph.status} />
              {activeTeamId === session.team_id ? (
                <span className="runtag" title="You're editing the team this session is running">running</span>
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  title="This session is running a DIFFERENT team. Click to switch it to the team you're editing."
                  onClick={async () => {
                    if (!activeTeamId || !activeSessionId) return;
                    const info = await api.rebindSession(activeSessionId, activeTeamId);
                    setSession(info);
                    await refresh();
                  }}
                >
                  ↻ Use for session
                </Button>
              )}
              <IconButton title="Save the current graph as a NEW team (a copy). Does not switch the running session." onClick={saveAs}>
                <CopyIcon />
              </IconButton>
            </div>

            <div className="hdiv" />
            {/* SESSION zone — what's RUNNING (a team bound to a repo) */}
            <div className="hzone">
              <span className="hcap">Session</span>
              <SessionSwitcher
                activeSessionId={activeSessionId}
                sessions={sessions}
                teams={teams}
                onSwitch={selectSession}
                onLaunched={async (id) => {
                  await refresh();
                  selectSession(id);
                }}
                flushSave={graph.flushSave}
              />
            </div>

            {/* right zone — session settings + view switch */}
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
              <button
                className="chip chip-button"
                title={
                  "Execution mode — parallel: model calls run concurrently; serial: "
                  + "one at a time (low-spec / single local model). Click to toggle."
                  + (session.harness === "opencode"
                      ? " (Native-harness setting; OpenCode manages its own concurrency.)"
                      : "")
                }
                onClick={() => {
                  const next = session.mode === "serial" ? "parallel" : "serial";
                  api.setMode(next).then(setSession);
                }}
              >
                {session.mode === "serial" ? "serial" : "parallel"}
              </button>
              <span
                className={(session.harness ?? "native") === "opencode" ? "chip chip-primary" : "chip"}
                title={
                  (session.harness ?? "native") === "opencode"
                    ? "OpenCode harness — agents run on a headless OpenCode server"
                    : "Native harness — agents run on the built-in Pydantic AI engine"
                }
              >
                {session.harness ?? "native"}
              </span>
              {/* sliding-pill segmented toggle between the canvas + task board */}
              <div
                style={{
                  position: "relative",
                  display: "flex",
                  background: "var(--surface-2)",
                  borderRadius: 999,
                  padding: 3,
                  width: 188,
                  border: "1px solid var(--border)",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    top: 3,
                    bottom: 3,
                    left: 3,
                    width: "calc(50% - 3px)",
                    borderRadius: 999,
                    background: "var(--primary)",
                    boxShadow: "var(--shadow-sm)",
                    transform: view === "board" ? "translateX(100%)" : "translateX(0)",
                    transition: "transform 0.18s cubic-bezier(0.4, 0, 0.2, 1)",
                  }}
                />
                {(["canvas", "board"] as const).map((v) => (
                  <button
                    key={v}
                    onClick={() => setView(v)}
                    style={{
                      position: "relative",
                      zIndex: 1,
                      flex: 1,
                      border: "none",
                      background: "transparent",
                      cursor: "pointer",
                      fontWeight: 600,
                      fontSize: 13,
                      padding: "6px 0",
                      color: view === v ? "#fff" : "var(--text-muted)",
                      transition: "color 0.18s ease",
                    }}
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
                waitingOnNames={waitingOnNames}
                waitingOn={waitingOn}
                onNodesChange={graph.onNodesChange}
                onEdgesChange={graph.onEdgesChange}
                onConnect={graph.onConnect}
                onSelectionChange={graph.onSelectionChange}
                onUpdateEdgeCurve={graph.updateEdgeCurve}
                addNode={graph.addNode}
                activeEdges={activeEdges}
              />
            ) : (
              <TaskBoard agents={agents} events={events} />
            )}
          </div>
          <Sidebar
            selected={graph.selectedSpec}
            onUpdate={graph.updateSpec}
            onDelete={graph.deleteNode}
            events={events}
            lifecycles={lifecycles}
            waitingOnNames={waitingOnNames}
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
