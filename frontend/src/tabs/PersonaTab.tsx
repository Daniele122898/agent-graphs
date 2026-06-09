import type { AgentSpec } from "../types";

// Persona tab: WHO the agent is. Backs Pydantic AI sticky `instructions`.
export default function PersonaTab({
  spec,
  onUpdate,
}: {
  spec: AgentSpec;
  onUpdate: (s: AgentSpec) => void;
}) {
  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <label style={{ fontSize: 12, fontWeight: 600 }}>
        Name
        <input
          value={spec.name}
          onChange={(e) => onUpdate({ ...spec, name: e.target.value })}
          style={{ width: "100%", padding: 6, marginTop: 4 }}
        />
      </label>

      <label style={{ fontSize: 12, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
        <input
          type="checkbox"
          checked={spec.is_entry_point}
          onChange={(e) => onUpdate({ ...spec, is_entry_point: e.target.checked })}
        />
        Entry point (can receive tasks directly)
      </label>

      <label style={{ fontSize: 12, fontWeight: 600 }}>
        Persona (sticky system prompt)
        <textarea
          value={spec.persona}
          onChange={(e) => onUpdate({ ...spec, persona: e.target.value })}
          rows={12}
          placeholder="You are a senior React engineer…"
          style={{ width: "100%", padding: 6, marginTop: 4, fontFamily: "inherit", resize: "vertical" }}
        />
      </label>
    </div>
  );
}
