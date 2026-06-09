// One EventSource connection to the session's SSE bus, shared across the UI.
// Events are accumulated in order; consumers (the Agent tab) filter by agent_id.
// Lifecycle is tracked per agent so canvas nodes / badges can reflect it later.

import { useEffect, useRef, useState } from "react";
import type { AgentLifecycle } from "./types";

export interface BusEvent {
  session_id: string;
  type: string;
  data: Record<string, unknown>;
}

const EVENT_TYPES = [
  "agent_lifecycle",
  "model_request",
  "thinking",
  "text",
  "tool_call",
  "tool_result",
  "todos",
  "agent_done",
  "agent_error",
];

export function useEvents() {
  const [events, setEvents] = useState<BusEvent[]>([]);
  const [lifecycles, setLifecycles] = useState<Record<string, AgentLifecycle>>({});
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource("/events");
    esRef.current = es;
    const handler = (e: MessageEvent) => {
      try {
        const parsed = JSON.parse(e.data) as BusEvent;
        setEvents((prev) => [...prev, parsed]);
        const agentId = parsed.data?.agent_id as string | undefined;
        if (parsed.type === "agent_lifecycle" && agentId) {
          setLifecycles((p) => ({ ...p, [agentId]: parsed.data.lifecycle as AgentLifecycle }));
        } else if (parsed.type === "agent_done" && agentId) {
          setLifecycles((p) => ({ ...p, [agentId]: "done" }));
        } else if (parsed.type === "agent_error" && agentId) {
          setLifecycles((p) => ({ ...p, [agentId]: "blocked" }));
        }
      } catch {
        /* ignore malformed frame */
      }
    };
    EVENT_TYPES.forEach((t) => es.addEventListener(t, handler as EventListener));
    es.onerror = () => {
      /* EventSource auto-reconnects */
    };
    return () => es.close();
  }, []);

  return { events, lifecycles };
}
