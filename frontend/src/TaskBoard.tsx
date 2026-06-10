import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type TaskRow } from "./api";
import NewTaskDialog from "./NewTaskDialog";
import type { BusEvent } from "./useEvents";

// Session-level Kanban board of tasks. Columns follow the status lifecycle;
// sub-tasks (parent_task_id) nest under their parent. Refreshes on task_status.
const COLUMNS: { key: string; label: string; statuses: string[] }[] = [
  { key: "queued", label: "Queued", statuses: ["queued"] },
  { key: "running", label: "Running", statuses: ["running", "needs_revision"] },
  { key: "review", label: "Needs review", statuses: ["needs_review"] },
  { key: "blocked", label: "Blocked", statuses: ["blocked"] },
  { key: "done", label: "Done", statuses: ["done", "failed", "cancelled"] },
];

const STATUS_COLOR: Record<string, string> = {
  queued: "#9aa4b2",
  running: "#15803d",
  needs_revision: "#b45309",
  needs_review: "#2563eb",
  blocked: "#c2341d",
  done: "#15803d",
  failed: "#c2341d",
  cancelled: "#9aa4b2",
};

interface AgentLite {
  id: string;
  name: string;
  is_entry_point: boolean;
}

export default function TaskBoard({ agents, events }: { agents: AgentLite[]; events: BusEvent[] }) {
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const refresh = useCallback(() => {
    api.listTasks().then((r) => setTasks(r.tasks)).catch(() => {});
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  const statusEventCount = useMemo(() => events.filter((e) => e.type === "task_status").length, [events]);
  useEffect(() => { refresh(); }, [statusEventCount, refresh]);

  return (
    <div style={{ height: "100%", overflowY: "auto", padding: 18, display: "flex", flexDirection: "column", gap: 14 }}>
      <NewTaskDialog agents={agents} onCreated={refresh} />

      <div style={{ display: "flex", gap: 12, flex: 1, minHeight: 0 }}>
        {COLUMNS.map((col) => {
          const colTasks = tasks.filter((t) => col.statuses.includes(t.status) && !t.parent_task_id);
          return (
            <div key={col.key} style={{ flex: 1, background: "var(--surface-2)", borderRadius: "var(--r)", padding: 10, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
                <span style={{ fontSize: 12, fontWeight: 650, color: "var(--text)" }}>{col.label}</span>
                <span className="chip" style={{ padding: "1px 7px" }}>{colTasks.length}</span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {colTasks.map((t) => (
                  <TaskCard key={t.id} task={t} subtasks={tasks.filter((s) => s.parent_task_id === t.id)} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TaskCard({ task, subtasks }: { task: TaskRow; subtasks: TaskRow[] }) {
  return (
    <div className="card" style={{ padding: 10, borderRadius: "var(--r-sm)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: STATUS_COLOR[task.status] ?? "#9aa4b2", flexShrink: 0 }} />
        <strong style={{ fontSize: 12.5 }}>{task.title}</strong>
      </div>
      <div className="mono" style={{ fontSize: 10.5, color: "var(--text-faint)", marginTop: 5 }}>
        → {task.assigned_agent_id} · {task.completion_signal}
      </div>
      {task.result && (
        <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6, maxHeight: 64, overflow: "hidden" }}>{task.result}</div>
      )}
      {subtasks.map((s) => (
        <div key={s.id} className="muted" style={{ fontSize: 11, marginTop: 5, paddingLeft: 10 }}>
          ↳ {s.title} ({s.status})
        </div>
      ))}
    </div>
  );
}
