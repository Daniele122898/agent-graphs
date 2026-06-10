import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type HistoryRow } from "../api";
import { Button, Chip, TextArea } from "../ui";
import type React from "react";
import type { BusEvent } from "../useEvents";
import type { AgentLifecycle } from "../types";

const LIFECYCLE_TONE: Record<AgentLifecycle, "default" | "primary" | "success" | "warning" | "danger"> = {
  idle: "default",
  running: "success",
  "waiting-on-agent": "warning",
  blocked: "danger",
  done: "primary",
};

// Agent tab: the live work. Give the agent a prompt, then watch it stream
// thinking / text / tool calls / results, with a live todo checklist. This is
// the "observe + interject" surface (interjection mid-run lands in Phase 3).

interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

const TODO_MARK: Record<TodoItem["status"], string> = {
  pending: "☐",
  in_progress: "◐",
  completed: "☑",
};

export default function AgentTab({
  agentId,
  events,
  lifecycle,
}: {
  agentId: string;
  events: BusEvent[];
  lifecycle: AgentLifecycle;
}) {
  const [prompt, setPrompt] = useState("");
  const [posting, setPosting] = useState(false);
  // The persisted conversation — the context the model actually resumes with.
  // Loaded once per agent; live SSE events are appended after the load point
  // (everything older is already inside the stored history).
  const [history, setHistory] = useState<{ instructions: string[]; rows: HistoryRow[] } | null>(null);
  // Live-tail cutoff by event *seq*, never array index — the events array can
  // reset (session switch, hook remount) while seq keeps counting, so an index
  // would point past everything and silently hide all new events.
  const [baselineSeq, setBaselineSeq] = useState(0);
  const [working, setWorking] = useState<"clear" | "summarize" | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const eventsRef = useRef(events);
  eventsRef.current = events;

  const loadHistory = useCallback(() => {
    const evs = eventsRef.current;
    setBaselineSeq(evs.length ? (evs[evs.length - 1].seq ?? 0) : 0);
    api.agentHistory(agentId)
      .then((h) => setHistory({ instructions: h.instructions, rows: h.rows }))
      .catch(() => setHistory(null));
  }, [agentId]);

  useEffect(() => {
    setHistory(null);
    setHistoryError(null);
    loadHistory();
  }, [loadHistory]);

  const mine = useMemo(() => events.filter((e) => e.data?.agent_id === agentId), [events, agentId]);
  const live = useMemo(
    () => events.filter((e) => (e.seq ?? 0) > baselineSeq && e.data?.agent_id === agentId),
    [events, baselineSeq, agentId]
  );
  const todos = useMemo(() => {
    const last = [...mine].reverse().find((e) => e.type === "todos");
    return (last?.data?.todos as TodoItem[] | undefined) ?? [];
  }, [mine]);

  const busy = lifecycle === "running" || lifecycle === "waiting-on-agent";
  const hasConversation = (history?.rows.length ?? 0) > 0 || live.length > 0;

  const clearHistory = async () => {
    if (!window.confirm("Clear this agent's entire conversation? Its persona, capabilities and environment are rebuilt on every request, so only the conversation is forgotten.")) return;
    setWorking("clear");
    setHistoryError(null);
    try {
      await api.clearAgentHistory(agentId);
      loadHistory();
    } catch (e) {
      setHistoryError(String(e));
    } finally {
      setWorking(null);
    }
  };

  const summarize = async () => {
    setWorking("summarize");
    setHistoryError(null);
    try {
      await api.summarizeAgentHistory(agentId);
      loadHistory();
    } catch (e) {
      setHistoryError(String(e));
    } finally {
      setWorking(null);
    }
  };

  const run = async () => {
    if (!prompt.trim()) return;
    setPosting(true);
    try {
      await api.runAgent(agentId, prompt);
      setPrompt("");
    } finally {
      setPosting(false);
    }
  };

  const stop = async () => {
    await api.stopAgent(agentId);
  };

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, height: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="field-label" style={{ margin: 0 }}>STATUS</span>
        <Chip tone={LIFECYCLE_TONE[lifecycle]}>{lifecycle}</Chip>
        <span style={{ flex: 1 }} />
        <Button size="sm" onClick={clearHistory} disabled={busy || working !== null || !hasConversation} title="Forget the whole conversation; keep the agent's identity">
          {working === "clear" ? "Clearing…" : "Clear"}
        </Button>
        <Button size="sm" onClick={summarize} disabled={busy || working !== null || !hasConversation} title="Compress the conversation into a model-written summary">
          {working === "summarize" ? "Summarizing…" : "Summarize"}
        </Button>
      </div>
      {historyError && (
        <div style={{ fontSize: 12, color: "#991b1b", background: "#fee2e2", borderRadius: "var(--r-sm)", padding: "6px 10px" }}>
          {historyError}
        </div>
      )}

      <TextArea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={3}
        placeholder={busy ? "Interject a message (runs after the current step)…" : "Give this agent a task…"}
      />
      <div style={{ display: "flex", gap: 8 }}>
        <Button variant="primary" onClick={run} disabled={posting || !prompt.trim()}>
          {posting ? "Starting…" : busy ? "Interject" : "Run"}
        </Button>
        {busy && (
          <Button variant="danger" onClick={stop}>
            Stop
          </Button>
        )}
      </div>

      {todos.length > 0 && (
        <div className="card" style={{ padding: 10 }}>
          <div className="field-label">TODOS</div>
          {todos.map((t, i) => (
            <div key={i} style={{ fontSize: 13, color: t.status === "completed" ? "var(--text-faint)" : "var(--text)", textDecoration: t.status === "completed" ? "line-through" : "none" }}>
              {TODO_MARK[t.status]} {t.content}
            </div>
          ))}
        </div>
      )}

      <div style={{ flex: 1, overflowY: "auto", fontSize: 13, display: "flex", flexDirection: "column", gap: 8, paddingTop: 4 }}>
        {history && history.instructions.length > 0 && (
          <ExpandableRow
            icon="⚙️"
            summary={
              <span>
                <strong>System context</strong>{" "}
                <span style={{ color: "var(--text-muted)" }}>
                  — {history.instructions.length} sections, rebuilt and sent with every model request
                </span>
              </span>
            }
            detail={history.instructions.join("\n\n──────────\n\n")}
            tone="result"
          />
        )}
        {history?.rows.map((r, i) => (
          <EventRow key={`h${i}`} event={historyRowToEvent(r)} />
        ))}
        {history && history.rows.length > 0 && live.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-faint)", fontSize: 11 }}>
            <span style={{ flex: 1, height: 1, background: "var(--border)" }} />
            live
            <span style={{ flex: 1, height: 1, background: "var(--border)" }} />
          </div>
        )}
        {live.map((e, i) => (
          <EventRow key={i} event={e} />
        ))}
      </div>
    </div>
  );
}

// Stored history rows reuse the live-event renderer so the transcript reads
// the same whether it streamed in or was reloaded from persistence.
function historyRowToEvent(r: HistoryRow): BusEvent {
  const TYPE: Record<HistoryRow["kind"], string> = {
    user: "user_message",
    thinking: "thinking",
    text: "text",
    tool_call: "tool_call",
    tool_result: "tool_result",
    retry: "retry",
    system: "thinking",
  };
  const data: Record<string, unknown> =
    r.kind === "tool_call"
      ? { tool: r.tool, args: r.args }
      : r.kind === "tool_result"
        ? { tool: r.tool, result: r.text }
        : { text: r.text };
  return { session_id: "", type: TYPE[r.kind], data };
}

// A chat bubble. `side` controls alignment (user right, agent left).
function Bubble({
  side,
  bg,
  color = "#111827",
  children,
  mono = false,
}: {
  side: "left" | "right";
  bg: string;
  color?: string;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div style={{ display: "flex", justifyContent: side === "right" ? "flex-end" : "flex-start" }}>
      <div
        style={{
          maxWidth: "85%",
          background: bg,
          color,
          borderRadius: 12,
          borderBottomRightRadius: side === "right" ? 2 : 12,
          borderBottomLeftRadius: side === "left" ? 2 : 12,
          padding: "7px 11px",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontFamily: mono ? "ui-monospace, monospace" : "inherit",
          fontSize: mono ? 12 : 13,
        }}
      >
        {children}
      </div>
    </div>
  );
}

// One-line, human-readable summary of a tool call — the full args stay one
// click away. Tuned per tool so the transcript reads like a work log.
function toolSummary(tool: string, args: Record<string, unknown> | undefined): string {
  const a = (k: string) => (args?.[k] != null ? String(args[k]) : "");
  switch (tool) {
    case "read_file":
      return a("start_line") ? `${a("path")} :${a("start_line")}–${a("end_line") || "end"}` : a("path");
    case "write_file":
      return a("path");
    case "edit_file":
      return `${a("path")} :${a("start")}–${a("end")}`;
    case "list_dir":
      return a("path") || ".";
    case "grep":
      return `"${a("pattern")}"${a("path") ? ` in ${a("path")}` : ""}`;
    case "run_bash":
      return truncate(a("command"), 90);
    case "ask_agent":
      return `${a("target_id")} — ${truncate(a("question"), 90)}`;
    case "write_todos": {
      const todos = args?.todos;
      return Array.isArray(todos) ? `${todos.length} item${todos.length === 1 ? "" : "s"}` : "";
    }
    default:
      return truncate(JSON.stringify(args ?? {}), 90);
  }
}

const TOOL_ICON: Record<string, string> = {
  read_file: "📖",
  write_file: "📝",
  edit_file: "✏️",
  list_dir: "📁",
  grep: "🔍",
  run_bash: "💻",
  ask_agent: "🤝",
  write_todos: "☑️",
};

// A compact, expandable row for a tool call or its result: a readable summary
// line; click to reveal the full payload.
function ExpandableRow({
  icon,
  summary,
  detail,
  tone = "call",
}: {
  icon: string;
  summary: React.ReactNode;
  detail: string;
  tone?: "call" | "result";
}) {
  const [open, setOpen] = useState(false);
  return (
    <div
      style={{
        borderLeft: `2px solid ${tone === "call" ? "var(--primary)" : "var(--border-strong, #d1d5db)"}`,
        marginLeft: 2,
        paddingLeft: 9,
      }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 6,
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          font: "inherit",
          fontSize: 12.5,
          color: tone === "call" ? "var(--text)" : "var(--text-muted)",
          textAlign: "left",
          width: "100%",
        }}
        title={open ? "Collapse" : "Expand"}
      >
        <span style={{ flexShrink: 0 }}>{icon}</span>
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
          {summary}
        </span>
        <span style={{ color: "var(--text-faint)", fontSize: 10, flexShrink: 0 }}>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <pre
          className="mono"
          style={{
            margin: "6px 0 2px",
            padding: 8,
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            fontSize: 11.5,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 260,
            overflowY: "auto",
            color: "var(--text)",
          }}
        >
          {detail}
        </pre>
      )}
    </div>
  );
}

function EventRow({ event }: { event: BusEvent }) {
  const d = event.data;
  switch (event.type) {
    case "user_message":
      return (
        <Bubble side="right" bg="#2563eb" color="white">
          {String(d.text)}
        </Bubble>
      );
    case "thinking":
      return (
        <Bubble side="left" bg="transparent" color="var(--text-muted)">
          <span style={{ fontStyle: "italic" }}>💭 {String(d.text)}</span>
        </Bubble>
      );
    case "text":
      return (
        <Bubble side="left" bg="#f3f4f6">
          {String(d.text)}
        </Bubble>
      );
    case "tool_call": {
      const tool = String(d.tool);
      return (
        <ExpandableRow
          icon={TOOL_ICON[tool] ?? "🛠"}
          summary={
            <>
              <strong>{tool}</strong>
              {"  "}
              <span className="mono" style={{ color: "var(--text-muted)", fontSize: 11.5 }}>
                {toolSummary(tool, d.args as Record<string, unknown>)}
              </span>
            </>
          }
          detail={JSON.stringify(d.args, null, 2)}
          tone="call"
        />
      );
    }
    case "tool_result": {
      const result = String(d.result);
      return (
        <ExpandableRow
          icon="↳"
          summary={<span className="mono" style={{ fontSize: 11.5 }}>{truncate(firstLine(result), 110)}</span>}
          detail={truncate(result, 4000)}
          tone="result"
        />
      );
    }
    case "agent_done":
      // The final text already appears as its own bubble — render completion
      // as a thin marker instead of repeating the whole output.
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#166534", fontSize: 11.5 }}>
          <span style={{ flex: 1, height: 1, background: "#bbe7c8" }} />
          ✓ run complete
          <span style={{ flex: 1, height: 1, background: "#bbe7c8" }} />
        </div>
      );
    case "agent_error":
      return (
        <Bubble side="left" bg="#fee2e2" color="#991b1b">
          ✗ {String(d.error)}
        </Bubble>
      );
    case "retry":
      // a harness nudge stored in history (tool error / validation feedback)
      return (
        <Bubble side="right" bg="#fef3c7" color="#92400e">
          ⟲ {String(d.text)}
        </Bubble>
      );
    default:
      return null;
  }
}

function firstLine(s: string): string {
  const i = s.indexOf("\n");
  return i === -1 ? s : s.slice(0, i);
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
