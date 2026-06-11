// TypeScript mirrors of the backend domain shapes (backend/domain/models.py).
// Kept in sync by hand; the round-trip API test on the backend guards the wire
// format, and these types guard the frontend's use of it.

export type FilesystemLevel = "none" | "read" | "read-write";
export type SessionMode = "parallel" | "serial";
export type AgentLifecycle =
  | "idle"
  | "running"
  | "waiting-on-agent"
  | "waiting-on-user"
  | "blocked"
  | "done";

export interface Capabilities {
  filesystem: FilesystemLevel;
  read_paths: string[];
  write_paths: string[];
  bash: boolean;
}

export interface AgentSpec {
  id: string;
  name: string;
  persona: string;
  model: string;
  is_entry_point: boolean;
  capabilities: Capabilities;
}

export interface GraphNode {
  spec: AgentSpec;
  position: { x: number; y: number };
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  curve?: number; // signed midpoint bend, set by dragging on the canvas
}

export interface TeamGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface SessionInfo {
  id: string;
  team_id: string;
  repo_path: string;
  mode: SessionMode;
  status: string;
}

export function defaultCapabilities(): Capabilities {
  return { filesystem: "read-write", read_paths: ["**"], write_paths: ["**"], bash: true };
}

// Strip the provider prefix ("lmstudio:", "local:", "openai:") from a model
// string, leaving the bare model id used by LM Studio lookups.
export function bareModelId(model: string): string {
  const i = model.indexOf(":");
  return i === -1 ? model : model.slice(i + 1);
}
