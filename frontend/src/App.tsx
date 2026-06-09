import { useEffect, useState } from "react";
import Canvas from "./Canvas";
import Sidebar from "./Sidebar";
import { api } from "./api";
import type { AgentSpec, SessionInfo } from "./types";

// The control room. Phase 1: a React Flow canvas (team graph editor) + the
// five-tab sidebar. A top bar shows the current session. Task board, live
// streaming, and multi-session switching arrive in later phases.
export default function App() {
  const [selected, setSelected] = useState<AgentSpec | null>(null);
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
          <Canvas onSelect={setSelected} />
        </div>
        <Sidebar selected={selected} />
      </div>
    </div>
  );
}
