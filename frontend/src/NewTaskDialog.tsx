import { useState } from "react";
import { api } from "./api";

interface AgentLite {
  id: string;
  name: string;
  is_entry_point: boolean;
}

type SignalKind = "self_reported" | "reviewer" | "check";

// Task intake: prompt + which agent (defaults to an entry point) + completion
// signal (self / reviewer agent / check command). That's the whole intake.
export default function NewTaskDialog({ agents, onCreated }: { agents: AgentLite[]; onCreated: () => void }) {
  const entryDefault = agents.find((a) => a.is_entry_point)?.id ?? agents[0]?.id ?? "";
  const [prompt, setPrompt] = useState("");
  const [agentId, setAgentId] = useState(entryDefault);
  const [signalKind, setSignalKind] = useState<SignalKind>("self_reported");
  const [signalArg, setSignalArg] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!prompt.trim()) return;
    const completion_signal =
      signalKind === "self_reported" ? "self_reported" : `${signalKind}:${signalArg}`;
    setBusy(true);
    try {
      await api.createTask({ prompt, assigned_agent_id: agentId, completion_signal });
      setPrompt("");
      setSignalArg("");
      onCreated();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 12, background: "white", display: "flex", flexDirection: "column", gap: 8 }}>
      <strong style={{ fontSize: 13 }}>New task</strong>
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={2}
        placeholder="What should the team do?"
        style={{ padding: 6, fontFamily: "inherit", resize: "vertical" }}
      />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", fontSize: 12 }}>
        <label>
          assign&nbsp;
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
                {a.is_entry_point ? " ⭐" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          done when&nbsp;
          <select value={signalKind} onChange={(e) => setSignalKind(e.target.value as SignalKind)}>
            <option value="self_reported">self-reported</option>
            <option value="reviewer">reviewer agent</option>
            <option value="check">check command</option>
          </select>
        </label>
        {signalKind === "reviewer" && (
          <select value={signalArg} onChange={(e) => setSignalArg(e.target.value)}>
            <option value="">(pick reviewer)</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        )}
        {signalKind === "check" && (
          <input
            value={signalArg}
            onChange={(e) => setSignalArg(e.target.value)}
            placeholder="pytest -q"
            style={{ flex: 1, minWidth: 120, padding: 4 }}
          />
        )}
        <button onClick={submit} disabled={busy || !prompt.trim()} style={{ padding: "4px 12px", cursor: "pointer" }}>
          {busy ? "…" : "Create"}
        </button>
      </div>
    </div>
  );
}
