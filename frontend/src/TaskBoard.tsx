import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type TaskRow } from "./api";
import NewTaskDialog from "./NewTaskDialog";
import type { BusEvent } from "./useEvents";

// Session-level Kanban board of tasks. Columns follow the status lifecycle;
// sub-tasks (parent_task_id) nest under their parent. Refreshes when a
// task_status event arrives on the SSE stream.
const COLUMNS: { key: string; label: string; statuses: string[] }[] = [
  { key: "queued", label: "Queued", statuses: ["queued"] },
  { key: "running", label: "Running", statuses: ["running", "needs_revision"] },
  { key: "review", label: "Needs review", statuses: ["needs_review"] },
  { key: "blocked", label: "Blocked", statuses: ["blocked"] },
  { key: "done", label: "Done", statuses: ["done", "failed", "cancelled"] },
];

const STATUS_COLOR: Record<string, string> = {
  queued: "#9ca3af",
  running: "#22c55e",
  needs_revision: "#f59e0b",
  needs_review: "#3b82f6",
  blocked: "#ef4444",
  done: "#16a34a",
  failed: "#ef4444",
  cancelled: "#9ca3af",
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

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Refetch whenever a task_status event has been seen (cheap; tasks are few).
  const statusEventCount = useMemo(() => events.filter((e) => e.type === "task_status").length, [events]);
  useEffect(() => {
    refresh();
  }, [statusEventCount, refresh]);

  return (
    <div style={{ height: "100%", overflowY: "auto", padding: 16, fontFamily: "system-ui, sans-serif", display: "flex", flexDirection: "column", gap: 12 }}>
      <NewTaskDialog agents={agents} onCreated={refresh} />

      <div style={{ display: "flex", gap: 12, flex: 1, minHeight: 0 }}>
        {COLUMNS.map((col) => {
          const colTasks = tasks.filter((t) => col.statuses.includes(t.status) && !t.parent_task_id);
          return (
            <div key={col.key} style={{ flex: 1, background: "#f3f4f6", borderRadius: 8, padding: 8, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 8 }}>
                {col.label} ({colTasks.length})
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
    <div style={{ background: "white", borderRadius: 6, padding: 8, boxShadow: "0 1px 2px rgba(0,0,0,0.08)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: STATUS_COLOR[task.status] ?? "#9ca3af" }} />
        <strong style={{ fontSize: 12 }}>{task.title}</strong>
      </div>
      <div style={{ fontSize: 11, color: "#6b7280", marginTop: 4 }}>
        → {task.assigned_agent_id} · {task.completion_signal}
      </div>
      {task.result && (
        <div style={{ fontSize: 11, color: "#374151", marginTop: 4, maxHeight: 60, overflow: "hidden" }}>{task.result}</div>
      )}
      {subtasks.map((s) => (
        <div key={s.id} style={{ fontSize: 11, marginTop: 4, paddingLeft: 10, color: "#6b7280" }}>
          ↳ {s.title} ({s.status})
        </div>
      ))}
    </div>
  );
}
