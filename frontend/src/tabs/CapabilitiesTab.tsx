import { useEffect, useState } from "react";
import { api, type LMStudioModel } from "../api";
import { Button, Field, Select, TextInput } from "../ui";
import type { AgentSpec, Capabilities, FilesystemLevel } from "../types";

// Capabilities tab: WHAT the agent can touch, plus WHICH model powers it.
function presetForLevel(level: FilesystemLevel): Pick<Capabilities, "read_paths" | "write_paths"> {
  if (level === "none") return { read_paths: [], write_paths: [] };
  if (level === "read") return { read_paths: ["**"], write_paths: [] };
  return { read_paths: ["**"], write_paths: ["**"] };
}

export default function CapabilitiesTab({
  spec,
  onUpdate,
}: {
  spec: AgentSpec;
  onUpdate: (s: AgentSpec) => void;
}) {
  const caps = spec.capabilities;
  const [advanced, setAdvanced] = useState(false);
  const [models, setModels] = useState<LMStudioModel[]>([]);

  useEffect(() => {
    api.lmstudioModels().then((r) => setModels(r.models)).catch(() => setModels([]));
  }, []);

  const setCaps = (patch: Partial<Capabilities>) => onUpdate({ ...spec, capabilities: { ...caps, ...patch } });
  const setLevel = (level: FilesystemLevel) => setCaps({ filesystem: level, ...presetForLevel(level) });

  return (
    <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
      <Field label="Model">
        <Select value={spec.model} onChange={(e) => onUpdate({ ...spec, model: e.target.value })}>
          {!models.some((m) => `lmstudio:${m.id}` === spec.model) && (
            <option value={spec.model}>{spec.model}</option>
          )}
          {models.map((m) => (
            <option key={m.id} value={`lmstudio:${m.id}`}>
              {m.id}
              {m.capabilities?.includes("tool_use") ? "  🛠" : "  ⚠ no tool calls"}
            </option>
          ))}
        </Select>
        {(() => {
          // A model without LM Studio's tool_use capability cannot function-call:
          // tool invocations come out as plain text and silently do nothing.
          const sel = models.find((m) => `lmstudio:${m.id}` === spec.model);
          return sel && !sel.capabilities?.includes("tool_use") ? (
            <div style={{ marginTop: 6, fontSize: 12, color: "#92400e", background: "#fef3c7", borderRadius: "var(--r-sm)", padding: "7px 10px" }}>
              ⚠ <strong>{sel.id}</strong> has no <code className="mono">tool_use</code> capability in LM Studio — it
              cannot call tools, so this agent won't be able to read/write files, run bash, or consult teammates.
              Pick a model marked 🛠 (e.g. <code className="mono">qwen/qwen3.5-9b</code>).
            </div>
          ) : null;
        })()}
      </Field>

      <Field label="Filesystem access">
        <Select value={caps.filesystem} onChange={(e) => setLevel(e.target.value as FilesystemLevel)}>
          <option value="none">none</option>
          <option value="read">read only</option>
          <option value="read-write">read & write</option>
        </Select>
      </Field>

      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
        <input type="checkbox" checked={caps.bash} onChange={(e) => setCaps({ bash: e.target.checked })}
          style={{ accentColor: "var(--primary)", width: 15, height: 15 }} />
        Allow <code className="mono">bash</code>
      </label>

      <Button variant="ghost" size="sm" onClick={() => setAdvanced((a) => !a)} style={{ alignSelf: "flex-start" }}>
        {advanced ? "▾" : "▸"} Advanced path globs
      </Button>
      {advanced && (
        <>
          <Field label="read_paths (comma-separated)">
            <TextInput value={caps.read_paths.join(", ")} onChange={(e) => setCaps({ read_paths: splitGlobs(e.target.value) })} />
          </Field>
          <Field label="write_paths (comma-separated)">
            <TextInput value={caps.write_paths.join(", ")} onChange={(e) => setCaps({ write_paths: splitGlobs(e.target.value) })} />
          </Field>
        </>
      )}
    </div>
  );
}

function splitGlobs(v: string): string[] {
  return v.split(",").map((s) => s.trim()).filter(Boolean);
}
