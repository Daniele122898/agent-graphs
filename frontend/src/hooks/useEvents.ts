// One EventSource connection to the session's SSE bus, shared across the UI.
// Events are accumulated in order; consumers (the Agent tab) filter by agent_id.
// Lifecycle is tracked per agent so canvas nodes / badges can reflect it later.

import { useEffect, useRef, useState } from "react";
import type { AgentLifecycle } from "../lib/types";

export interface BusEvent {
  session_id: string;
  type: string;
  data: Record<string, unknown>;
  /** Monotonic client-side arrival number. Survives the events array being
   * reset (session switch, hook remount), so "events after X" comparisons
   * must use seq, never array indices. */
  seq?: number;
}

// Module-scoped so it keeps counting across remounts and session switches.
let seqCounter = 0;

const EVENT_TYPES = [
  "user_message",
  "agent_lifecycle",
  "model_request",
  "thinking",
  "text",
  "tool_call",
  "tool_result",
  "todos",
  "agent_done",
  "agent_error",
  "a2a_message",
  "task_status",
  "user_question",
  "user_question_done",
];

export function useEvents(sessionId: string | null) {
  const [events, setEvents] = useState<BusEvent[]>([]);
  const [lifecycles, setLifecycles] = useState<Record<string, AgentLifecycle>>({});
  // Edges with a recent delegation message, keyed `from->to`, for canvas animation.
  const [activeEdges, setActiveEdges] = useState<Set<string>>(new Set());
  const esRef = useRef<EventSource | null>(null);
  // Pending edge-deactivation timers, cleared on unmount/session switch so a
  // late timer never updates state for a connection that's gone.
  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());

  useEffect(() => {
    // Reset accumulated state when switching sessions.
    setEvents([]);
    setLifecycles({});
    setActiveEdges(new Set());
    if (!sessionId) return; // nothing to subscribe to yet (boot/reconcile)
    const es = new EventSource(`/events?session_id=${sessionId}`);
    esRef.current = es;
    const handler = (e: MessageEvent) => {
      try {
        const parsed = JSON.parse(e.data) as BusEvent;
        parsed.seq = ++seqCounter;
        setEvents((prev) => [...prev, parsed]);
        const agentId = parsed.data?.agent_id as string | undefined;
        if (parsed.type === "agent_lifecycle" && agentId) {
          setLifecycles((p) => ({ ...p, [agentId]: parsed.data.lifecycle as AgentLifecycle }));
        } else if (parsed.type === "agent_done" && agentId) {
          setLifecycles((p) => ({ ...p, [agentId]: "done" }));
        } else if (parsed.type === "agent_error" && agentId) {
          setLifecycles((p) => ({ ...p, [agentId]: "blocked" }));
        } else if (parsed.type === "a2a_message") {
          const key = `${parsed.data.from}->${parsed.data.to}`;
          setActiveEdges((p) => new Set(p).add(key));
          const timer = setTimeout(() => {
            timersRef.current.delete(timer);
            setActiveEdges((p) => {
              const next = new Set(p);
              next.delete(key);
              return next;
            });
          }, 2500);
          timersRef.current.add(timer);
        }
      } catch {
        /* ignore malformed frame */
      }
    };
    EVENT_TYPES.forEach((t) => es.addEventListener(t, handler as EventListener));
    es.onerror = () => {
      /* EventSource auto-reconnects */
    };
    return () => {
      EVENT_TYPES.forEach((t) => es.removeEventListener(t, handler as EventListener));
      es.close();
      timersRef.current.forEach((t) => clearTimeout(t));
      timersRef.current.clear();
    };
  }, [sessionId]);

  return { events, lifecycles, activeEdges };
}
