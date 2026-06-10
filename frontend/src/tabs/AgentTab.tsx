import { useMemo, useState } from "react";
import { api } from "../api";
import { Button, Chip, TextArea } from "../ui";
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

  const mine = useMemo(() => events.filter((e) => e.data?.agent_id === agentId), [events, agentId]);
  const todos = useMemo(() => {
    const last = [...mine].reverse().find((e) => e.type === "todos");
    return (last?.data?.todos as TodoItem[] | undefined) ?? [];
  }, [mine]);

  const busy = lifecycle === "running" || lifecycle === "waiting-on-agent";

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
      </div>

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
        {mine.map((e, i) => (
          <EventRow key={i} event={e} />
        ))}
      </div>
    </div>
  );
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
        <Bubble side="left" bg="transparent" color="#9ca3af">
          <span style={{ fontStyle: "italic" }}>💭 {String(d.text)}</span>
        </Bubble>
      );
    case "text":
      return (
        <Bubble side="left" bg="#f3f4f6">
          {String(d.text)}
        </Bubble>
      );
    case "tool_call":
      return (
        <Bubble side="left" bg="#eef2ff" mono>
          🛠 {String(d.tool)}({JSON.stringify(d.args)})
        </Bubble>
      );
    case "tool_result":
      return (
        <Bubble side="left" bg="transparent" color="#6b7280" mono>
          ↳ {truncate(String(d.result), 200)}
        </Bubble>
      );
    case "agent_done":
      return (
        <Bubble side="left" bg="#dcfce7" color="#166534">
          ✓ {String(d.output)}
        </Bubble>
      );
    case "agent_error":
      return (
        <Bubble side="left" bg="#fee2e2" color="#991b1b">
          ✗ {String(d.error)}
        </Bubble>
      );
    default:
      return null;
  }
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
