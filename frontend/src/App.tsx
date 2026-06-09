import { useEffect, useState } from "react";

interface Health {
  status: string;
  tables: string[];
  sessions: number;
}

interface SessionInfo {
  id: string;
  team_id: string;
  repo_path: string;
  mode: string;
  status: string;
}

// Phase 0 hello-world: prove the frontend can reach the backend and that the
// auto-created session is live. The real control room (canvas, sidebar, task
// board) lands in later phases.
export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetch("/health").then((r) => r.json()),
      fetch("/api/session").then((r) => r.json()),
    ])
      .then(([h, s]) => {
        setHealth(h);
        setSession(s);
      })
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <h1>Agent Graphs</h1>
      <p style={{ color: "#666" }}>A local multi-agent software team in a folder.</p>

      {error && <p style={{ color: "crimson" }}>Backend unreachable: {error}</p>}

      {health && (
        <section>
          <h2>Backend</h2>
          <p>
            Status: <strong>{health.status}</strong> · Sessions: {health.sessions}
          </p>
          <p>Tables: {health.tables.join(", ")}</p>
        </section>
      )}

      {session && (
        <section>
          <h2>Current session</h2>
          <ul>
            <li>id: {session.id}</li>
            <li>repo: {session.repo_path}</li>
            <li>mode: {session.mode}</li>
            <li>status: {session.status}</li>
          </ul>
        </section>
      )}
    </div>
  );
}
