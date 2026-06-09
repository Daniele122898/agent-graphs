import { useEffect, useState } from "react";
import { api, type LMStudioModel } from "../api";
import type { AgentSpec, Capabilities, FilesystemLevel } from "../types";

// Capabilities tab: WHAT the agent can touch, plus WHICH model powers it
// (model selection is a capability — "what brain is it running on").
// The simple filesystem level is a preset that fills the path globs; the
// advanced disclosure lets you override them.

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

  const setCaps = (patch: Partial<Capabilities>) =>
    onUpdate({ ...spec, capabilities: { ...caps, ...patch } });

  const setLevel = (level: FilesystemLevel) => setCaps({ filesystem: level, ...presetForLevel(level) });

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14, fontSize: 13 }}>
      <label style={{ fontWeight: 600 }}>
        Model
        <select
          value={spec.model}
          onChange={(e) => onUpdate({ ...spec, model: e.target.value })}
          style={{ width: "100%", padding: 6, marginTop: 4 }}
        >
          {!models.some((m) => `lmstudio:${m.id}` === spec.model) && (
            <option value={spec.model}>{spec.model}</option>
          )}
          {models.map((m) => (
            <option key={m.id} value={`lmstudio:${m.id}`}>
              lmstudio:{m.id}
              {m.capabilities?.includes("tool_use") ? " 🛠" : ""}
            </option>
          ))}
        </select>
      </label>

      <label style={{ fontWeight: 600 }}>
        Filesystem
        <select
          value={caps.filesystem}
          onChange={(e) => setLevel(e.target.value as FilesystemLevel)}
          style={{ width: "100%", padding: 6, marginTop: 4 }}
        >
          <option value="none">none</option>
          <option value="read">read</option>
          <option value="read-write">read-write</option>
        </select>
      </label>

      <label style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={caps.bash} onChange={(e) => setCaps({ bash: e.target.checked })} />
        bash
      </label>

      <button
        onClick={() => setAdvanced((a) => !a)}
        style={{ alignSelf: "flex-start", fontSize: 12, background: "none", border: "none", color: "#2563eb", cursor: "pointer", padding: 0 }}
      >
        {advanced ? "▼" : "▶"} advanced path globs
      </button>
      {advanced && (
        <>
          <label style={{ fontWeight: 600 }}>
            read_paths (comma-separated globs)
            <input
              value={caps.read_paths.join(", ")}
              onChange={(e) => setCaps({ read_paths: splitGlobs(e.target.value) })}
              style={{ width: "100%", padding: 6, marginTop: 4 }}
            />
          </label>
          <label style={{ fontWeight: 600 }}>
            write_paths (comma-separated globs)
            <input
              value={caps.write_paths.join(", ")}
              onChange={(e) => setCaps({ write_paths: splitGlobs(e.target.value) })}
              style={{ width: "100%", padding: 6, marginTop: 4 }}
            />
          </label>
        </>
      )}
    </div>
  );
}

function splitGlobs(v: string): string[] {
  return v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}
