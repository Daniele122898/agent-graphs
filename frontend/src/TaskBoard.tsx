import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type TaskRow } from "./api";
import NewTaskDialog from "./NewTaskDialog";
import { Button, Chip, IconButton } from "./ui";
import type { BusEvent } from "./useEvents";

// Session-level Kanban board of tasks. Columns follow the status lifecycle;
// sub-tasks (parent_task_id) nest under their parent. Refreshes on task_status.
// Clicking a card opens a detail drawer (full prompt, gate, result, todos).
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

const TODO_MARK: Record<string, string> = { pending: "☐", in_progress: "◐", completed: "☑" };

interface AgentLite {
  id: string;
  name: string;
  is_entry_point: boolean;
}

export default function TaskBoard({ agents, events }: { agents: AgentLite[]; events: BusEvent[] }) {
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const refresh = useCallback(() => {
    api.listTasks().then((r) => setTasks(r.tasks)).catch(() => {});
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  const statusEventCount = useMemo(() => events.filter((e) => e.type === "task_status").length, [events]);
  useEffect(() => { refresh(); }, [statusEventCount, refresh]);

  const agentName = useCallback(
    (id: string) => agents.find((a) => a.id === id)?.name ?? id,
    [agents]
  );
  const selected = tasks.find((t) => t.id === selectedId) ?? null;

  return (
    <div style={{ height: "100%", display: "flex", minHeight: 0 }}>
      <div style={{ flex: 1, minWidth: 0, overflowY: "auto", padding: 18, display: "flex", flexDirection: "column", gap: 14 }}>
        <NewTaskDialog agents={agents} onCreated={refresh} />

        {/* columns keep a readable min width; the row scrolls horizontally when
            the detail drawer + a wide sidebar squeeze the board */}
        <div style={{ display: "flex", gap: 12, flex: 1, minHeight: 0, overflowX: "auto" }}>
          {COLUMNS.map((col) => {
            const colTasks = tasks.filter((t) => col.statuses.includes(t.status) && !t.parent_task_id);
            return (
              <div key={col.key} style={{ flex: 1, background: "var(--surface-2)", borderRadius: "var(--r)", padding: 10, minWidth: 150 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
                  <span style={{ fontSize: 12, fontWeight: 650, color: "var(--text)" }}>{col.label}</span>
                  <span className="chip" style={{ padding: "1px 7px" }}>{colTasks.length}</span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {colTasks.map((t) => (
                    <TaskCard
                      key={t.id}
                      task={t}
                      agentName={agentName}
                      subtasks={tasks.filter((s) => s.parent_task_id === t.id)}
                      selected={t.id === selectedId}
                      onSelect={() => setSelectedId(t.id === selectedId ? null : t.id)}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {selected && (
        <TaskDetail
          task={selected}
          agentName={agentName}
          subtasks={tasks.filter((s) => s.parent_task_id === selected.id)}
          onSelectTask={setSelectedId}
          onRetry={() => api.retryTask(selected.id).then(refresh).catch(() => {})}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}

function TaskCard({
  task,
  subtasks,
  agentName,
  selected,
  onSelect,
}: {
  task: TaskRow;
  subtasks: TaskRow[];
  agentName: (id: string) => string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      aria-label={`Task: ${task.title}`}
      className="card"
      style={{
        padding: 10,
        borderRadius: "var(--r-sm)",
        textAlign: "left",
        cursor: "pointer",
        font: "inherit",
        border: selected ? "1px solid var(--primary)" : "1px solid transparent",
        width: "100%",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: STATUS_COLOR[task.status] ?? "#9aa4b2", flexShrink: 0 }} />
        <strong style={{ fontSize: 12.5 }}>{task.title}</strong>
      </div>
      {/* untitled tasks get title = prompt[:60]; skip the redundant preview then */}
      {!task.prompt.startsWith(task.title) && (
        <div
          style={{
            fontSize: 11.5,
            color: "var(--text-muted)",
            marginTop: 5,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {task.prompt}
        </div>
      )}
      <div className="mono" style={{ fontSize: 10.5, color: "var(--text-faint)", marginTop: 6 }}>
        → {agentName(task.assigned_agent_id)} · {task.completion_signal}
      </div>
      {subtasks.map((s) => (
        <div key={s.id} className="muted" style={{ fontSize: 11, marginTop: 5, paddingLeft: 10 }}>
          ↳ {s.title} ({s.status})
        </div>
      ))}
    </button>
  );
}

// Right-hand drawer with everything the board card can't show: the full
// prompt, the completion gate, the result (success vs error), live todos,
// timestamps, and clickable sub-tasks.
function TaskDetail({
  task,
  subtasks,
  agentName,
  onSelectTask,
  onRetry,
  onClose,
}: {
  task: TaskRow;
  subtasks: TaskRow[];
  agentName: (id: string) => string;
  onSelectTask: (id: string) => void;
  onRetry: () => void;
  onClose: () => void;
}) {
  const failed = task.status === "blocked" || task.status === "failed" || task.result.startsWith("error:");
  return (
    <div
      style={{
        width: 380,
        flexShrink: 0,
        borderLeft: "1px solid var(--border)",
        background: "var(--surface)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 14px", borderBottom: "1px solid var(--border)" }}>
        <span style={{ width: 9, height: 9, borderRadius: "50%", background: STATUS_COLOR[task.status] ?? "#9aa4b2" }} />
        <strong style={{ fontSize: 13, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {task.title}
        </strong>
        <Chip>{task.status}</Chip>
        {task.status === "blocked" && (
          <Button variant="primary" size="sm" onClick={onRetry} aria-label="Retry task">
            ↻ Retry
          </Button>
        )}
        <IconButton aria-label="Close task details" onClick={onClose}>✕</IconButton>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 14, fontSize: 13 }}>
        <Section label="Prompt">
          <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", background: "var(--surface-2)", borderRadius: "var(--r-sm)", padding: 10 }}>
            {task.prompt}
          </div>
        </Section>

        <Section label="Assignee · completion gate">
          <div>
            {agentName(task.assigned_agent_id)}{" "}
            <span className="mono" style={{ fontSize: 11, color: "var(--text-faint)" }}>{task.assigned_agent_id}</span>
          </div>
          <div className="mono" style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{task.completion_signal}</div>
        </Section>

        {task.todos.length > 0 && (
          <Section label="Todos">
            {task.todos.map((t, i) => (
              <div key={i} style={{ color: t.status === "completed" ? "var(--text-faint)" : "var(--text)" }}>
                {TODO_MARK[t.status]} {t.content}
              </div>
            ))}
          </Section>
        )}

        {task.result && (
          <Section label={failed ? "Result (failed)" : "Result"}>
            <div
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                padding: 10,
                borderRadius: "var(--r-sm)",
                background: failed ? "#fee2e2" : "#dcfce7",
                color: failed ? "#991b1b" : "#166534",
              }}
            >
              {task.result}
            </div>
          </Section>
        )}

        {subtasks.length > 0 && (
          <Section label="Sub-tasks">
            {subtasks.map((s) => (
              <button
                key={s.id}
                onClick={() => onSelectTask(s.id)}
                className="card"
                style={{ padding: 8, textAlign: "left", cursor: "pointer", font: "inherit", width: "100%", marginBottom: 6 }}
              >
                <span style={{ width: 7, height: 7, borderRadius: "50%", display: "inline-block", marginRight: 6, background: STATUS_COLOR[s.status] ?? "#9aa4b2" }} />
                {s.title} <span className="muted">({s.status})</span>
              </button>
            ))}
          </Section>
        )}

        <Section label="Timeline">
          <div className="mono" style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
            created {formatTs(task.created_at)}
            <br />
            updated {formatTs(task.updated_at)}
          </div>
        </Section>
      </div>
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="field-label" style={{ marginBottom: 5 }}>{label.toUpperCase()}</div>
      {children}
    </div>
  );
}

function formatTs(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}
