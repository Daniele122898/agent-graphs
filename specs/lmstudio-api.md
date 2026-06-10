# LM Studio REST API (notes)

Reference notes for the local LM Studio server at `http://127.0.0.1:1234`
(provided by Daniele from the official docs, 2026-06). LM Studio offers a
native REST API plus OpenAI-compatible and Anthropic-compatible endpoints.

## v1 REST API (LM Studio ≥ 0.4.0 — recommended)

Native endpoints live under `/api/v1/*` (the older `/api/v0/*` REST API still
exists; `stats.py` currently reads `/api/v0/models` for rich model metadata).

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/chat` | POST | Native chat (stateful chats, MCP, load/prompt-processing streaming events, per-request context length) |
| `/api/v1/models` | GET | List models |
| `/api/v1/models/load` | POST | **Load a model** |
| `/api/v1/models/unload` | POST | **Unload a model** |
| `/api/v1/models/download` | POST | Download a model |
| `/api/v1/models/download/status` | GET | Download progress |

v1 also adds authentication via API tokens and MCP-via-API.

## Inference endpoint comparison

| Feature | `/api/v1/chat` | `/v1/responses` | `/v1/chat/completions` | `/v1/messages` |
|---|---|---|---|---|
| Streaming | ✅ | ✅ | ✅ | ✅ |
| Stateful chat | ✅ | ✅ | ❌ | ❌ |
| Remote MCPs | ✅ | ✅ | ❌ | ❌ |
| Local (LM Studio) MCPs | ✅ | ✅ | ❌ | ❌ |
| **Custom tools** | ❌ | ✅ | ✅ | ✅ |
| Assistant messages in request | ❌ | ✅ | ✅ | ✅ |
| Model-load streaming events | ✅ | ❌ | ❌ | ❌ |
| Prompt-processing streaming events | ✅ | ❌ | ❌ | ❌ |
| Context length per request | ✅ | ❌ | ❌ | ❌ |

## Implications for agent-graphs

- **Our agents must keep using the OpenAI-compatible `/v1/chat/completions`**
  (what pydantic-ai's OpenAI provider speaks): the native `/api/v1/chat` does
  NOT support custom tools, which our whole toolset depends on.
- **Model switching is automatable**: `POST /api/v1/models/load` / `unload`
  let the control room (or a test harness) swap the loaded model. The user's
  laptop can only run ONE model at a time — always unload before loading
  another.
- `GET /api/v1/models` (or legacy `/api/v0/models`) reports `state:
  loaded|not-loaded`, quantization, max/loaded context length, and
  `capabilities` (e.g. `tool_use`) — useful for the Stats tab and for picking
  a tool-capable model.
