import { useMemo, useState } from "react";
import { api } from "../api";
import type { BusEvent } from "../useEvents";
import type { AgentLifecycle } from "../types";

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

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10, height: "100%" }}>
      <div style={{ fontSize: 12 }}>
        lifecycle: <strong>{lifecycle}</strong>
      </div>

      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={3}
        placeholder="Give this agent a task…"
        style={{ width: "100%", padding: 6, fontFamily: "inherit", resize: "vertical" }}
      />
      <button
        onClick={run}
        disabled={posting || !prompt.trim()}
        style={{ alignSelf: "flex-start", padding: "6px 14px", cursor: "pointer" }}
      >
        {posting ? "starting…" : "Run"}
      </button>

      {todos.length > 0 && (
        <div style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 8, background: "white" }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "#6b7280", marginBottom: 4 }}>TODOS</div>
          {todos.map((t, i) => (
            <div key={i} style={{ fontSize: 13, textDecoration: t.status === "completed" ? "line-through" : "none" }}>
              {TODO_MARK[t.status]} {t.content}
            </div>
          ))}
        </div>
      )}

      <div style={{ flex: 1, overflowY: "auto", fontSize: 13, display: "flex", flexDirection: "column", gap: 6 }}>
        {mine.map((e, i) => (
          <EventRow key={i} event={e} />
        ))}
      </div>
    </div>
  );
}

function EventRow({ event }: { event: BusEvent }) {
  const d = event.data;
  switch (event.type) {
    case "thinking":
      return <div style={{ fontStyle: "italic", color: "#9ca3af" }}>💭 {String(d.text)}</div>;
    case "text":
      return <div style={{ whiteSpace: "pre-wrap" }}>{String(d.text)}</div>;
    case "tool_call":
      return (
        <div style={{ background: "#f3f4f6", borderRadius: 6, padding: "4px 8px", fontFamily: "monospace", fontSize: 12 }}>
          🛠 {String(d.tool)}({JSON.stringify(d.args)})
        </div>
      );
    case "tool_result":
      return (
        <div style={{ color: "#6b7280", fontFamily: "monospace", fontSize: 11, paddingLeft: 12 }}>
          ↳ {truncate(String(d.result), 200)}
        </div>
      );
    case "agent_done":
      return <div style={{ color: "#16a34a", fontWeight: 600 }}>✓ {String(d.output)}</div>;
    case "agent_error":
      return <div style={{ color: "crimson" }}>✗ {String(d.error)}</div>;
    default:
      return null;
  }
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
