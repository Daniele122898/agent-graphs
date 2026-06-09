import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addEdge,
  Background,
  Controls,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import AgentNode from "./AgentNode";
import { api } from "./api";
import { fromReactFlow, toReactFlow, type RFNode } from "./graphMapping";
import { defaultCapabilities, type AgentSpec } from "./types";

const nodeTypes = { agent: AgentNode };

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

export default function Canvas({ onSelect }: { onSelect: (spec: AgentSpec | null) => void }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<RFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const loaded = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [status, setStatus] = useState("loading…");

  // Initial load from the backend.
  useEffect(() => {
    api
      .getGraph()
      .then((g) => {
        const { nodes: n, edges: e } = toReactFlow(g);
        setNodes(n);
        setEdges(e);
        loaded.current = true;
        setStatus("saved");
      })
      .catch((err) => setStatus(`load error: ${err}`));
  }, [setNodes, setEdges]);

  // Debounced persistence on any change (after the initial load).
  useEffect(() => {
    if (!loaded.current) return;
    setStatus("saving…");
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      api
        .putGraph(fromReactFlow(nodes, edges))
        .then(() => setStatus("saved"))
        .catch((err) => setStatus(`save error: ${err}`));
    }, 600);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [nodes, edges]);

  const onConnect = useCallback(
    (conn: Connection) => {
      setEdges((eds) =>
        addEdge({ ...conn, id: `e_${conn.source}_${conn.target}`, label: "" }, eds)
      );
    },
    [setEdges]
  );

  const addNode = useCallback(() => {
    setNodes((nds) => {
      const ids = new Set(nds.map((n) => n.id));
      const id = uniqueId(ids);
      const node: RFNode = {
        id,
        type: "agent",
        position: { x: 80 + Math.random() * 240, y: 80 + Math.random() * 240 },
        data: { spec: newAgentSpec(id) },
      };
      return [...nds, node];
    });
  }, [setNodes]);

  const onSelectionChange = useCallback(
    ({ nodes: selected }: { nodes: RFNode[] }) => {
      onSelect(selected.length === 1 ? selected[0].data.spec : null);
    },
    [onSelect]
  );

  const statusColor = useMemo(
    () => (status.includes("error") ? "crimson" : "#6b7280"),
    [status]
  );

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onSelectionChange={onSelectionChange}
        fitView
      >
        <Background />
        <Controls />
      </ReactFlow>

      <button
        onClick={addNode}
        title="Add agent"
        style={{
          position: "absolute",
          top: 12,
          left: 12,
          width: 40,
          height: 40,
          borderRadius: "50%",
          border: "none",
          background: "#2563eb",
          color: "white",
          fontSize: 22,
          cursor: "pointer",
          boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
          zIndex: 5,
        }}
      >
        +
      </button>

      <span
        style={{
          position: "absolute",
          top: 18,
          left: 64,
          fontSize: 12,
          color: statusColor,
          fontFamily: "system-ui, sans-serif",
        }}
      >
        {status}
      </span>
    </div>
  );
}
