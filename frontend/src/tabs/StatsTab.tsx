import { useEffect, useState } from "react";
import { api, type LMStudioModel } from "../api";
import { bareModelId, type AgentSpec } from "../types";

// Stats tab: HOW is it doing — observe-only. LM Studio model stats for the
// agent's model + token usage from completed runs. Flags the documented quirk
// where loaded_context_length is a small default below max_context_length.
export default function StatsTab({ spec }: { spec: AgentSpec }) {
  const [model, setModel] = useState<LMStudioModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [usage, setUsage] = useState<{ requests: number; input_tokens: number; output_tokens: number } | null>(null);

  useEffect(() => {
    api.lmstudioModels().then((r) => {
      setError(r.error);
      const id = bareModelId(spec.model);
      setModel(r.models.find((m) => m.id === id) ?? null);
    });
    api.usage(spec.id).then(setUsage).catch(() => setUsage(null));
  }, [spec.model, spec.id]);

  return (
    <div style={{ padding: 16, fontSize: 13, display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Model — {spec.model}</div>
        {error && <div style={{ color: "#9ca3af" }}>LM Studio unreachable ({error})</div>}
        {model ? (
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            <li>state: {model.state}</li>
            <li>arch: {model.arch}</li>
            <li>quantization: {model.quantization}</li>
            <li>capabilities: {model.capabilities?.join(", ") || "—"}</li>
            <li>
              context: {model.loaded_context_length} loaded / {model.max_context_length} max
              {model.loaded_context_length != null &&
                model.max_context_length != null &&
                model.loaded_context_length < model.max_context_length && (
                  <span style={{ color: "#d97706" }}> ⚠ raise in LM Studio to avoid truncation</span>
                )}
            </li>
          </ul>
        ) : (
          !error && <div style={{ color: "#9ca3af" }}>not a loaded LM Studio model</div>
        )}
      </div>

      <div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Usage (this session)</div>
        {usage ? (
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            <li>requests: {usage.requests}</li>
            <li>input tokens: {usage.input_tokens}</li>
            <li>output tokens: {usage.output_tokens}</li>
          </ul>
        ) : (
          <div style={{ color: "#9ca3af" }}>no runs yet</div>
        )}
      </div>
    </div>
  );
}
