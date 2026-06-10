import { Field, TextArea, TextInput } from "../ui";
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
    <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
      <Field label="Name">
        <TextInput value={spec.name} onChange={(e) => onUpdate({ ...spec, name: e.target.value })} />
      </Field>

      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={spec.is_entry_point}
          onChange={(e) => onUpdate({ ...spec, is_entry_point: e.target.checked })}
          style={{ accentColor: "var(--primary)", width: 15, height: 15 }}
        />
        Entry point <span className="muted">(can receive tasks directly)</span>
      </label>

      <Field label="Persona — sticky system prompt">
        <TextArea
          value={spec.persona}
          onChange={(e) => onUpdate({ ...spec, persona: e.target.value })}
          rows={12}
          placeholder="You are a senior React engineer…"
        />
      </Field>
    </div>
  );
}
