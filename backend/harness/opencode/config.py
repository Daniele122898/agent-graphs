"""Generate an OpenCode config (``opencode.json`` + custom tools) from a
``TeamGraph``. Pure: takes our domain shapes + provider settings and returns the
dict OpenCode reads; the server manager writes it to a temp config dir.

Mapping decisions:
- One OpenCode ``agent`` per ``AgentSpec`` (keyed by our agent id, so the agent
  name in OpenCode IS our agent id — the ask_agent callback uses it as asker).
- ``model`` = ``"<backend>/<name>"`` (e.g. ``lmstudio/qwen/qwen3.5-9b``).
- ``prompt`` = our OpenCode-flavored system prompt (see prompt.py).
- ``permission`` from ``Capabilities`` (read/edit/bash globs) PLUS
  ``question: allow`` (OpenCode defaults it to DENY — without this ask_user
  never fires) and ``task: deny`` (we delegate via our own ask_agent, not
  OpenCode subagents); ``webfetch``/``websearch`` denied to match native (no web
  tools).
- ``tools`` disables ``task``/``webfetch``/``websearch`` belt-and-suspenders;
  our ``ask_agent`` custom tool stays enabled.
- ``provider`` block from our config.py (LM Studio always; DeepSeek when keyed).
"""

from __future__ import annotations

from pathlib import Path

from ...domain.models import Capabilities, TeamGraph
from ...providers.deepseek import deepseek_api_key
from ...providers.lmstudio import lmstudio_base_url
from ...providers.registry import split_model_string
from .prompt import build_opencode_prompt

# The delegation callback tool. Reads its wiring from env vars the server
# manager injects when spawning OpenCode. ctx.agent is our agent id (OpenCode
# agents are named by our ids), so the asker is known without a session map.
ASK_AGENT_TOOL_TS = """\
import { tool } from "@opencode-ai/plugin"

// agent-graphs delegation bridge: hand the call back to our backend, which
// enforces neighbor/cycle/depth guards and runs the target agent on its own
// persistent session, then returns the answer.
export default tool({
  description:
    "Consult a teammate agent by id and get their concise answer. Use this when a question is outside your expertise; only the teammates listed in your instructions are reachable.",
  args: {
    target_id: tool.schema.string().describe("the teammate's agent id"),
    question: tool.schema.string().describe("the question to ask them"),
  },
  async execute(args, ctx) {
    const base = process.env.AGENT_GRAPHS_CALLBACK_URL
    const token = process.env.AGENT_GRAPHS_CALLBACK_TOKEN ?? ""
    const sessionId = process.env.AGENT_GRAPHS_SESSION_ID ?? ""
    const res = await fetch(`${base}/internal/ask_agent`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-ag-token": token },
      body: JSON.stringify({
        session_id: sessionId,
        asker_id: ctx.agent,
        target_id: args.target_id,
        question: args.question,
      }),
    })
    const text = await res.text()
    if (!res.ok) throw new Error(text || `ask_agent failed (${res.status})`)
    return text
  },
})
"""


def _glob_permission(allowed: bool, paths: list[str]) -> object:
    """Map a capability (allowed? + path globs) to an OpenCode permission value.
    ``"allow"``/``"deny"`` for the common full/none cases; a per-glob object
    (allow listed globs, deny the rest) for path-restricted agents."""
    if not allowed or not paths:
        return "deny"
    if paths == ["**"]:
        return "allow"
    rules = {g: "allow" for g in paths}
    rules["*"] = "deny"
    return rules


def capability_permission(caps: Capabilities) -> dict:
    """Capabilities → OpenCode agent ``permission`` object."""
    return {
        "read": _glob_permission(caps.can_read, caps.read_paths),
        "edit": _glob_permission(caps.can_write, caps.write_paths),
        "bash": "allow" if caps.bash else "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "task": "deny",        # we delegate via ask_agent, not OpenCode subagents
        "question": "allow",   # REQUIRED: default is deny, which kills ask_user
    }


def opencode_model_id(model_str: str) -> str:
    """``"lmstudio:qwen/qwen3.5-9b"`` -> ``"lmstudio/qwen/qwen3.5-9b"``."""
    backend, name = split_model_string(model_str)
    return f"{backend}/{name}" if backend else name


def _provider_block(graph: TeamGraph) -> dict:
    """Provider config covering the model backends the team's agents use."""
    providers: dict = {}
    used: dict[str, set[str]] = {}
    for node in graph.nodes:
        backend, name = split_model_string(node.spec.model)
        used.setdefault(backend, set()).add(name)

    if "lmstudio" in used:
        providers["lmstudio"] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": "LM Studio (local)",
            "options": {"baseURL": lmstudio_base_url()},
            "models": {m: {} for m in sorted(used["lmstudio"])},
        }
    if "deepseek" in used:
        # DeepSeek is a built-in OpenCode provider (models.dev); we only inject
        # the key. Omit the block entirely if unconfigured (OpenCode will error
        # clearly on use rather than us shipping an empty key).
        key = deepseek_api_key()
        if key:
            providers["deepseek"] = {"options": {"apiKey": key}}
    return providers


def build_opencode_config(graph: TeamGraph, *, repo_root: Path) -> dict:
    """The full ``opencode.json`` dict for a session's team."""
    agents: dict = {}
    for node in graph.nodes:
        spec = node.spec
        agents[spec.id] = {
            "mode": "primary",
            "model": opencode_model_id(spec.model),
            "prompt": build_opencode_prompt(spec, graph, repo_root),
            "permission": capability_permission(spec.capabilities),
            "tools": {"task": False, "webfetch": False, "websearch": False},
        }
    config: dict = {
        "$schema": "https://opencode.ai/config.json",
        "agent": agents,
    }
    providers = _provider_block(graph)
    if providers:
        config["provider"] = providers
    return config
