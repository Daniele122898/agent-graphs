import { useState } from "react";
import { api } from "./api";
import { Button, Select, TextArea, TextInput } from "./ui";

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
    const completion_signal = signalKind === "self_reported" ? "self_reported" : `${signalKind}:${signalArg}`;
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
    <div className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <strong style={{ fontSize: 13 }}>New task</strong>
      <TextArea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={2} placeholder="What should the team do?" />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Select value={agentId} onChange={(e) => setAgentId(e.target.value)} style={{ width: "auto" }}>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>{a.name}{a.is_entry_point ? " ⭐" : ""}</option>
          ))}
        </Select>
        <Select value={signalKind} onChange={(e) => setSignalKind(e.target.value as SignalKind)} style={{ width: "auto" }}>
          <option value="self_reported">self-reported</option>
          <option value="reviewer">reviewer agent</option>
          <option value="check">check command</option>
        </Select>
        {signalKind === "reviewer" && (
          <Select value={signalArg} onChange={(e) => setSignalArg(e.target.value)} style={{ width: "auto" }}>
            <option value="">(pick reviewer)</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </Select>
        )}
        {signalKind === "check" && (
          <TextInput value={signalArg} onChange={(e) => setSignalArg(e.target.value)} placeholder="pytest -q" style={{ flex: 1, minWidth: 120, width: "auto" }} />
        )}
        <Button variant="primary" onClick={submit} disabled={busy || !prompt.trim()} style={{ marginLeft: "auto" }}>
          {busy ? "…" : "Create task"}
        </Button>
      </div>
    </div>
  );
}
