import { useEffect, useState } from "react";
import { api, type ProviderInfo, type ProviderModel } from "../../lib/api";
import { Button, Field, Select, TextInput } from "../../lib/ui";
import type { AgentSpec, Capabilities, FilesystemLevel } from "../../lib/types";

// Capabilities tab: WHAT the agent can touch, plus WHICH backend + model power
// it. The backend (provider) dropdown sits above the model dropdown; thinking
// controls appear only for backends that support them (provider metadata).
function presetForLevel(level: FilesystemLevel): Pick<Capabilities, "read_paths" | "write_paths"> {
  if (level === "none") return { read_paths: [], write_paths: [] };
  if (level === "read") return { read_paths: ["**"], write_paths: [] };
  return { read_paths: ["**"], write_paths: ["**"] };
}

function providerIdOf(model: string): string {
  const i = model.indexOf(":");
  const prefix = i === -1 ? "" : model.slice(0, i);
  return prefix === "local" ? "lmstudio" : prefix;
}

const warnBox: React.CSSProperties = {
  marginTop: 6,
  fontSize: 12,
  color: "#92400e",
  background: "#fef3c7",
  borderRadius: "var(--r-sm)",
  padding: "7px 10px",
};

export default function CapabilitiesTab({
  spec,
  onUpdate,
}: {
  spec: AgentSpec;
  onUpdate: (s: AgentSpec) => void;
}) {
  const caps = spec.capabilities;
  const [advanced, setAdvanced] = useState(false);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<ProviderModel[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);

  const providerId = providerIdOf(spec.model);
  const provider = providers.find((p) => p.id === providerId);

  useEffect(() => {
    api.providers().then((r) => setProviders(r.providers)).catch(() => setProviders([]));
  }, []);

  useEffect(() => {
    if (!provider) {
      setModels([]);
      setModelsError(null);
      return;
    }
    let cancelled = false;
    api
      .providerModels(provider.id)
      .then((r) => {
        if (cancelled) return;
        setModels(r.models);
        setModelsError(r.error);
      })
      .catch((e) => {
        if (cancelled) return;
        setModels([]);
        setModelsError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [provider?.id]);

  const setCaps = (patch: Partial<Capabilities>) => onUpdate({ ...spec, capabilities: { ...caps, ...patch } });
  const setLevel = (level: FilesystemLevel) => setCaps({ filesystem: level, ...presetForLevel(level) });

  const switchProvider = (id: string) => {
    const p = providers.find((x) => x.id === id);
    // Thinking semantics are backend-specific — reset the preference on switch.
    onUpdate({
      ...spec,
      model: `${id}:${p?.default_model ?? ""}`,
      thinking: null,
      thinking_effort: null,
    });
  };

  const selected = models.find((m) => `${providerId}:${m.id}` === spec.model);
  const thinkingValue = spec.thinking === true ? "on" : spec.thinking === false ? "off" : "default";

  return (
    <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
      <Field label="Backend">
        <Select value={provider ? provider.id : providerId} onChange={(e) => switchProvider(e.target.value)}>
          {!provider && <option value={providerId}>{providerId || "(none)"}</option>}
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
              {p.configured ? "" : "  ⚠ not configured"}
            </option>
          ))}
        </Select>
        {provider && !provider.configured && (
          <div style={warnBox}>
            ⚠ <strong>{provider.label}</strong> is not configured — {provider.hint}.
          </div>
        )}
      </Field>

      <Field label="Model">
        <Select value={spec.model} onChange={(e) => onUpdate({ ...spec, model: e.target.value })}>
          {!selected && <option value={spec.model}>{spec.model}</option>}
          {models.map((m) => (
            <option key={m.id} value={`${providerId}:${m.id}`}>
              {m.label}
              {m.tool_use === false ? "  ⚠ no tool calls" : m.tool_use ? "  🛠" : ""}
            </option>
          ))}
        </Select>
        {modelsError && (
          <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
            Couldn't list models: {modelsError}
          </div>
        )}
        {selected?.tool_use === false && (
          // A model that cannot function-call is useless as an agent: tool
          // invocations come out as plain text and silently do nothing.
          <div style={warnBox}>
            ⚠ <strong>{selected.id}</strong> cannot call tools on {provider?.label ?? providerId} — this
            agent won't be able to read/write files, run bash, or consult teammates. Pick a model
            marked 🛠 (e.g. <code className="mono">qwen/qwen3.5-9b</code>).
          </div>
        )}
      </Field>

      {provider?.thinking.toggleable && (
        <Field label="Thinking">
          <Select
            value={thinkingValue}
            onChange={(e) => {
              const v = e.target.value;
              onUpdate({
                ...spec,
                thinking: v === "default" ? null : v === "on",
                // effort only means something while thinking is possible
                thinking_effort: v === "off" ? null : spec.thinking_effort,
              });
            }}
          >
            <option value="default">backend default</option>
            <option value="on">on</option>
            <option value="off">off</option>
          </Select>
        </Field>
      )}
      {provider?.thinking.toggleable && provider.thinking.efforts.length > 0 && spec.thinking !== false && (
        <Field label="Thinking effort">
          <Select
            value={spec.thinking_effort ?? "default"}
            onChange={(e) =>
              onUpdate({
                ...spec,
                thinking_effort: e.target.value === "default" ? null : e.target.value,
              })
            }
          >
            <option value="default">backend default</option>
            {provider.thinking.efforts.map((eff) => (
              <option key={eff} value={eff}>
                {eff}
              </option>
            ))}
          </Select>
        </Field>
      )}

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
