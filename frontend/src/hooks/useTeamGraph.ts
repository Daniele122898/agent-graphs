// Encapsulates the team-graph editor state: React Flow nodes/edges, load from
// and debounced save to the backend, add-node, connect, and per-agent spec
// edits. Lifting this into a hook lets both the Canvas (presentational) and the
// Sidebar (spec editor) share one source of truth.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { addEdge, useEdgesState, useNodesState, type Connection, type Edge, type Node } from "@xyflow/react";
import { api, withRetry } from "../lib/api";
import { fromReactFlow, toReactFlow, type RFNode } from "../canvas/graphMapping";
import { defaultCapabilities, type AgentSpec } from "../lib/types";

function uniqueId(existing: Set<string>): string {
  let n = existing.size + 1;
  while (existing.has(`agent_${n}`)) n += 1;
  return `agent_${n}`;
}

function newAgentSpec(id: string): AgentSpec {
  return {
    id,
    name: "New Agent",
    persona: "",
    // must be a tool_use-capable model (see specs/lmstudio-api.md)
    model: "lmstudio:qwen/qwen3.5-9b",
    is_entry_point: false,
    capabilities: defaultCapabilities(),
  };
}

export function useTeamGraph(teamId: string | null) {
  const [nodes, setNodes, onNodesChange] = useNodesState<RFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [status, setStatus] = useState("loading…");
  const loaded = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // (Re)load whenever the active team changes (loading a library team into the
  // editor). Suppress the save effect until the new graph is in place.
  useEffect(() => {
    if (!teamId) return;
    loaded.current = false;
    setStatus("loading…");
    let cancelled = false;
    // Retried so a page load racing a backend --reload restart doesn't leave
    // the canvas empty (nodes AND links) until a manual refresh.
    withRetry(() => api.getTeamGraph(teamId))
      .then((g) => {
        if (cancelled) return;
        const { nodes: n, edges: e } = toReactFlow(g);
        setNodes(n);
        setEdges(e);
        loaded.current = true;
        setStatus("saved");
      })
      .catch((err) => {
        if (!cancelled) setStatus(`load error: ${err}`);
      });
    return () => {
      cancelled = true;
    };
  }, [teamId, setNodes, setEdges]);

  useEffect(() => {
    if (!loaded.current || !teamId) return;
    setStatus("saving…");
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      api
        .putTeamGraph(teamId, fromReactFlow(nodes, edges))
        .then(() => setStatus("saved"))
        .catch((err) => setStatus(`save error: ${err}`));
    }, 600);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [nodes, edges, teamId]);

  const onConnect = useCallback(
    (conn: Connection) =>
      setEdges((eds) => addEdge({ ...conn, id: `e_${conn.source}_${conn.target}`, label: "" }, eds)),
    [setEdges]
  );

  const addNode = useCallback(() => {
    setNodes((nds) => {
      const id = uniqueId(new Set(nds.map((n) => n.id)));
      return [
        ...nds,
        {
          id,
          type: "agent" as const,
          position: { x: 80 + Math.random() * 240, y: 80 + Math.random() * 240 },
          data: { spec: newAgentSpec(id) },
        },
      ];
    });
  }, [setNodes]);

  const updateSpec = useCallback(
    (spec: AgentSpec) =>
      setNodes((nds) => nds.map((n) => (n.id === spec.id ? { ...n, data: { spec } } : n))),
    [setNodes]
  );

  const updateEdgeLabel = useCallback(
    (edgeId: string, label: string) =>
      setEdges((eds) => eds.map((e) => (e.id === edgeId ? { ...e, label } : e))),
    [setEdges]
  );

  // Set the dragged bend of an edge (persisted via the debounced graph save).
  const updateEdgeCurve = useCallback(
    (edgeId: string, curve: number) =>
      setEdges((eds) => eds.map((e) => (e.id === edgeId ? { ...e, data: { ...e.data, curve } } : e))),
    [setEdges]
  );

  // Selecting an edge loads its OWNING (source) agent in the sidebar and
  // exposes the edge id so the sidebar can jump to the Links tab for editing.
  const onSelectionChange = useCallback(
    ({ nodes: selN, edges: selE }: { nodes: Node[]; edges: Edge[] }) => {
      if (selE.length === 1) {
        setSelectedEdgeId(selE[0].id);
        setSelectedId(selE[0].source);
      } else {
        setSelectedEdgeId(null);
        setSelectedId(selN.length === 1 ? selN[0].id : null);
      }
    },
    []
  );

  const selectedSpec = useMemo(
    () => nodes.find((n) => n.id === selectedId)?.data.spec ?? null,
    [nodes, selectedId]
  );

  return {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onConnect,
    onSelectionChange,
    addNode,
    updateSpec,
    updateEdgeLabel,
    updateEdgeCurve,
    selectedId,
    selectedEdgeId,
    selectedSpec,
    status,
    snapshot: () => fromReactFlow(nodes, edges),
  };
}
