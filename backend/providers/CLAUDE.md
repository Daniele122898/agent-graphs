# backend/providers/ — model backends

`base.py` defines `ModelBackend` (the abstraction: configured? / list_models /
build / thinking support); `lmstudio.py` and `deepseek.py` implement it;
`registry.py` holds `BACKENDS`, resolves a per-agent model string
(`lmstudio:qwen/qwen3.5-9b`, `deepseek:deepseek-v4-flash`, ...) to a Pydantic
AI model instance, and maps thinking preferences to backend-specific
`ModelSettings`.

- **Adding a backend** = subclass `ModelBackend`, add an instance to
  `BACKENDS` — the UI dropdown, model picker, and thinking controls light up
  from its metadata; nothing else changes. Model strings stay
  `"<backend-id>:<name>"` so persisted specs never migrate.
- **Credentials/endpoints come from `backend/config.py`** (gitignored
  `config.yml`, env overrides; `config.example.yml` is the committed shape) —
  read at call time, never committed, never cached at import.
- **The resolved model is injected, never constructed inside agent logic** —
  that's the seam tests use to swap in `FunctionModel`. `resolve_model` keeps
  the `(model_str) -> Model` signature (the test seam); thinking preferences
  travel separately via `thinking_settings(...)` into `Agent(model_settings=)`.
- **Thinking-parts handling is OPPOSITE between the two backends** — don't
  "unify" it: LM Studio sets `openai_chat_send_back_thinking_parts=False`
  (re-feeding reasoning melts small local models), while the DeepSeek profile
  uses `'field'` because the v4 API **requires** prior `reasoning_content` in
  tool-call loops (400 without it).
- **DeepSeek thinking** is a request parameter (`extra_body.thinking`:
  enabled/disabled + `reasoning_effort` high|max), NOT the legacy
  deepseek-chat/deepseek-reasoner model split (deprecated 2026-07-24).
- **LM Studio quirks** (hard-won, see also specs/lmstudio-api.md): only models
  with the `tool_use` capability can function-call — others emit tool calls as
  text that silently does nothing, and the capability flag alone isn't
  sufficient (`unsloth/gemma-4-12b-it-qat` claims tool_use but its jinja
  template crashes on any tools request — probe a new model with a tiny tools
  request before trusting it). `openai_chat_send_back_thinking_parts=False`:
  local thinking models return `reasoning_content`, and echoing it back
  re-feeds the whole reasoning trace to a small model on a weak machine.
  Finite read timeout (`AGENT_GRAPHS_LOCAL_READ_TIMEOUT`, default 600s): a
  model unloaded mid-call must fail the run, not hang it forever.
- The LM Studio base URL comes from `AGENT_GRAPHS_LMSTUDIO_URL`
  (default `http://127.0.0.1:1234/v1`); the stats API lives at the host root
  under `/api/v0`, not under `/v1`.
