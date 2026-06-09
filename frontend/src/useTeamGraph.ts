// Encapsulates the team-graph editor state: React Flow nodes/edges, load from
// and debounced save to the backend, add-node, connect, and per-agent spec
// edits. Lifting this into a hook lets both the Canvas (presentational) and the
// Sidebar (spec editor) share one source of truth.

import { useCallback, useEffect, useRef, useState } from "react";
import { addEdge, useEdgesState, useNodesState, type Connection, type Edge } from "@xyflow/react";
import { api } from "./api";
import { fromReactFlow, toReactFlow, type RFNode } from "./graphMapping";
import { defaultCapabilities, type AgentSpec } from "./types";

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
    model: "lmstudio:qwen2.5-coder-7b-instruct-mlx",
    is_entry_point: false,
    capabilities: defaultCapabilities(),
  };
}

export function useTeamGraph(teamId: string | null) {
  const [nodes, setNodes, onNodesChange] = useNodesState<RFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [status, setStatus] = useState("loading…");
  const loaded = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // (Re)load whenever the active team changes (loading a library team into the
  // editor). Suppress the save effect until the new graph is in place.
  useEffect(() => {
    if (!teamId) return;
    loaded.current = false;
    setStatus("loading…");
    api
      .getTeamGraph(teamId)
      .then((g) => {
        const { nodes: n, edges: e } = toReactFlow(g);
        setNodes(n);
        setEdges(e);
        loaded.current = true;
        setStatus("saved");
      })
      .catch((err) => setStatus(`load error: ${err}`));
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

  const onSelectionChange = useCallback(
    ({ nodes: sel }: { nodes: RFNode[] }) => setSelectedId(sel.length === 1 ? sel[0].id : null),
    []
  );

  const selectedSpec = nodes.find((n) => n.id === selectedId)?.data.spec ?? null;

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
    selectedId,
    selectedSpec,
    status,
    snapshot: () => fromReactFlow(nodes, edges),
  };
}
