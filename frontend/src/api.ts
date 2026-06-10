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

// The active session id is module state so session-scoped calls target the
// selected session without threading it through every component. The SSE URL
// builds it explicitly (see useEvents). null = the backend default session.
let currentSessionId: string | null = null;
export function setActiveSession(id: string | null): void {
  currentSessionId = id;
}
export function activeSessionId(): string | null {
  return currentSessionId;
}
function withSession(path: string): string {
  if (!currentSessionId) return path;
  return path + (path.includes("?") ? "&" : "?") + `session_id=${currentSessionId}`;
}

export const api = {
  health: () => fetch("/health").then(json<{ status: string; tables: string[]; sessions: number }>),
  session: () => fetch(withSession("/api/session")).then(json<SessionInfo>),
  listSessions: () =>
    fetch("/api/sessions").then(json<{ sessions: SessionInfo[]; default_session_id: string }>),
  launchSession: (team_id: string, repo_path: string, mode: "parallel" | "serial") =>
    fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_id, repo_path, mode }),
    }).then(json<SessionInfo & { warning: string | null }>),
  setMode: (mode: "parallel" | "serial") =>
    fetch(withSession("/api/session/mode"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    }).then(json<SessionInfo>),
  listTeams: () => fetch("/api/teams").then(json<{ teams: TeamRow[] }>),
  // Omit graph to get the backend's starter team (one lead agent).
  createTeam: (name: string, graph?: TeamGraph) =>
    fetch("/api/teams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(graph ? { name, graph } : { name }),
    }).then(json<TeamRow>),
  getTeamGraph: (teamId: string) => fetch(`/api/teams/${teamId}/graph`).then(json<TeamGraph>),
  putTeamGraph: (teamId: string, graph: TeamGraph) =>
    fetch(`/api/teams/${teamId}/graph`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(graph),
    }).then(json<TeamGraph>),
  runAgent: (agentId: string, prompt: string) =>
    fetch(withSession(`/api/agent/${agentId}/run`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    }).then(json<{ status: string; agent_id: string }>),
  stopAgent: (agentId: string) =>
    fetch(withSession(`/api/agent/${agentId}/stop`), { method: "POST" }).then(
      json<{ status: string; agent_id: string }>
    ),
  lmstudioModels: () =>
    fetch("/api/stats/models").then(json<{ models: LMStudioModel[]; error: string | null }>),
  usage: (agentId: string) =>
    fetch(withSession(`/api/stats/usage/${agentId}`)).then(
      json<{ requests: number; input_tokens: number; output_tokens: number }>
    ),
  listTasks: () => fetch(withSession("/api/tasks")).then(json<{ tasks: TaskRow[] }>),
  createTask: (body: {
    prompt: string;
    title?: string;
    assigned_agent_id?: string | null;
    completion_signal?: string;
  }) =>
    fetch(withSession("/api/tasks"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<TaskRow>),
  retryTask: (taskId: string) =>
    fetch(`/api/tasks/${taskId}/retry`, { method: "POST" }).then(
      json<{ status: string; task_id: string }>
    ),
};

export interface TaskRow {
  id: string;
  title: string;
  prompt: string;
  assigned_agent_id: string;
  status: string;
  completion_signal: string;
  todos: { content: string; status: "pending" | "in_progress" | "completed" }[];
  parent_task_id: string | null;
  result: string;
  created_at: string;
  updated_at: string;
}

export interface TeamRow {
  id: string;
  name: string;
}

export interface LMStudioModel {
  id: string;
  type?: string;
  arch?: string;
  quantization?: string;
  state?: string;
  max_context_length?: number;
  loaded_context_length?: number;
  capabilities?: string[];
}
