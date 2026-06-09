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
  setMode: (mode: "parallel" | "serial") =>
    fetch("/api/session/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    }).then(json<SessionInfo>),
  listTeams: () => fetch("/api/teams").then(json<{ teams: TeamRow[] }>),
  createTeam: (name: string, graph: TeamGraph) =>
    fetch("/api/teams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, graph }),
    }).then(json<TeamRow>),
  getTeamGraph: (teamId: string) => fetch(`/api/teams/${teamId}/graph`).then(json<TeamGraph>),
  putTeamGraph: (teamId: string, graph: TeamGraph) =>
    fetch(`/api/teams/${teamId}/graph`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(graph),
    }).then(json<TeamGraph>),
  runAgent: (agentId: string, prompt: string) =>
    fetch(`/api/agent/${agentId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    }).then(json<{ status: string; agent_id: string }>),
  stopAgent: (agentId: string) =>
    fetch(`/api/agent/${agentId}/stop`, { method: "POST" }).then(
      json<{ status: string; agent_id: string }>
    ),
  lmstudioModels: () =>
    fetch("/api/stats/models").then(json<{ models: LMStudioModel[]; error: string | null }>),
  usage: (agentId: string) =>
    fetch(`/api/stats/usage/${agentId}`).then(
      json<{ requests: number; input_tokens: number; output_tokens: number }>
    ),
  listTasks: () => fetch("/api/tasks").then(json<{ tasks: TaskRow[] }>),
  createTask: (body: {
    prompt: string;
    title?: string;
    assigned_agent_id?: string | null;
    completion_signal?: string;
  }) =>
    fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<TaskRow>),
};

export interface TaskRow {
  id: string;
  title: string;
  prompt: string;
  assigned_agent_id: string;
  status: string;
  completion_signal: string;
  parent_task_id: string | null;
  result: string;
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
