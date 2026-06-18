import { useEffect, useState, type ReactNode } from "react";
import { api, type LMStudioModel } from "../../lib/api";
import { bareModelId, type AgentSpec } from "../../lib/types";

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

  const truncated =
    model?.loaded_context_length != null &&
    model?.max_context_length != null &&
    model.loaded_context_length < model.max_context_length;

  return (
    <div style={{ padding: 18, fontSize: 13, display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <div className="field-label">MODEL</div>
        <div className="mono" style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 10, wordBreak: "break-all" }}>{spec.model}</div>
        {error && <div style={{ color: "var(--text-faint)" }}>LM Studio unreachable ({error})</div>}
        {model ? (
          <KV
            rows={[
              ["State", model.state],
              ["Architecture", model.arch],
              ["Quantization", model.quantization],
              ["Capabilities", model.capabilities?.join(", ") || "—"],
              [
                "Context",
                <>
                  {model.loaded_context_length} loaded / {model.max_context_length} max
                  {truncated && <span style={{ color: "var(--warning)" }}> · ⚠ raise in LM Studio</span>}
                </>,
              ],
            ]}
          />
        ) : (
          !error && <div style={{ color: "var(--text-faint)" }}>Not a loaded LM Studio model.</div>
        )}
      </div>

      <div>
        <div className="field-label">USAGE (THIS SESSION)</div>
        {usage ? (
          <KV
            rows={[
              ["Requests", String(usage.requests)],
              ["Input tokens", usage.input_tokens.toLocaleString()],
              ["Output tokens", usage.output_tokens.toLocaleString()],
            ]}
          />
        ) : (
          <div style={{ color: "var(--text-faint)" }}>No runs yet.</div>
        )}
      </div>
    </div>
  );
}

// A compact, scannable label/value grid — replaces the raw debug-style bullet
// lists so the tab reads to the same standard as the rest of the app.
function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {rows.map(([k, v], i) => (
        <div key={i} style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
          <span style={{ color: "var(--text-faint)", minWidth: 104, flexShrink: 0 }}>{k}</span>
          <span style={{ color: "var(--text)", minWidth: 0 }}>{v}</span>
        </div>
      ))}
    </div>
  );
}
