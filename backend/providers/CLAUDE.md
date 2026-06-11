# backend/providers/ — model backends

`registry.py` resolves a per-agent model string (`lmstudio:qwen/qwen3.5-9b`,
`openai:gpt-4o`, ...) to a Pydantic AI model instance; `lmstudio.py` owns the
LM Studio specifics (model construction + the `/api/v0/models` stats fetch).

- **The resolved model is injected, never constructed inside agent logic** —
  that's the seam tests use to swap in `FunctionModel`.
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
