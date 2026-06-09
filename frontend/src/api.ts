// All backend calls. Relative URLs work in dev via the Vite proxy (vite.config.ts).
// As the multi-session UI lands later, session_id/team_id will be threaded
// through here; for now the backend exposes "the current one".

import type { SessionInfo, TeamGraph } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch("/health").then(json<{ status: string; tables: string[]; sessions: number }>),
  session: () => fetch("/api/session").then(json<SessionInfo>),
  getGraph: () => fetch("/api/team/graph").then(json<TeamGraph>),
  putGraph: (graph: TeamGraph) =>
    fetch("/api/team/graph", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(graph),
    }).then(json<TeamGraph>),
};
