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

// Boot-critical fetches retry briefly: the backend runs under --reload during
// development, so a page load can race a restart window — without a retry the
// app sits with an empty canvas (no nodes/links) until a manual refresh.
export async function withRetry<T>(fn: () => Promise<T>, attempts = 6, delayMs = 700): Promise<T> {
  let last: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  throw last;
}

export const api = {
  health: () => fetch("/health").then(json<{ status: string; tables: string[]; sessions: number }>),
  session: () => fetch(withSession("/api/session")).then(json<SessionInfo>),
  listSessions: () =>
    fetch("/api/sessions").then(json<{ sessions: SessionInfo[]; default_session_id: string }>),
  launchSession: (
    team_id: string,
    repo_path: string,
    mode: "parallel" | "serial",
    harness: "native" | "opencode" = "native",
  ) =>
    fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_id, repo_path, mode, harness }),
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
  agentHistory: (agentId: string) =>
    fetch(withSession(`/api/agent/${agentId}/history`)).then(
      json<{ instructions: string[]; rows: HistoryRow[]; message_count: number }>
    ),
  clearAgentHistory: (agentId: string) =>
    fetch(withSession(`/api/agent/${agentId}/history/clear`), { method: "POST" }).then(
      json<{ status: string }>
    ),
  summarizeAgentHistory: (agentId: string) =>
    fetch(withSession(`/api/agent/${agentId}/history/summarize`), { method: "POST" }).then(
      json<{ status: string }>
    ),
  openQuestions: () =>
    fetch(withSession("/api/questions")).then(json<{ questions: OpenQuestion[] }>),
  answerQuestion: (questionId: string, answers: string[]) =>
    fetch(withSession(`/api/questions/${questionId}/answer`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    }).then(json<{ status: string }>),
  lmstudioModels: () =>
    fetch("/api/stats/models").then(json<{ models: LMStudioModel[]; error: string | null }>),
  providers: () => fetch("/api/providers").then(json<{ providers: ProviderInfo[] }>),
  providerModels: (providerId: string) =>
    fetch(`/api/providers/${providerId}/models`).then(
      json<{ models: ProviderModel[]; error: string | null }>
    ),
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

// One rendered row of an agent's stored conversation — same shapes as the
// live SSE events so the Agent tab renders past and live work identically.
export interface HistoryRow {
  kind: "user" | "thinking" | "text" | "tool_call" | "tool_result" | "retry" | "system";
  text?: string;
  tool?: string;
  args?: Record<string, unknown>;
}

// A pending ask_user call: the agent's run is parked until these are answered.
export interface OpenQuestion {
  id: string;
  agent_id: string;
  questions: { question: string; options: string[] }[];
  created_at: string;
}

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

// A model backend (LM Studio, DeepSeek, ...) as described by /api/providers.
export interface ProviderInfo {
  id: string;
  label: string;
  default_model: string;
  configured: boolean;
  hint: string; // what's missing when not configured (e.g. the config.yml key)
  thinking: { toggleable: boolean; efforts: string[] };
}

// One model offered by a backend. tool_use: false = cannot function-call
// (useless as an agent); null/undefined = unknown.
export interface ProviderModel {
  id: string;
  label: string;
  tool_use?: boolean | null;
}
