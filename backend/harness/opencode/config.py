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

import os
from pathlib import Path

from ...domain.models import Capabilities, TeamGraph
from ...providers.deepseek import deepseek_api_key
from ...providers.lmstudio import lmstudio_base_url
from ...providers.registry import split_model_string
from .prompt import build_opencode_prompt

# The delegation callback tool source, kept as a real .ts file (proper syntax
# highlighting / lint) under tools/ and read at import. The harness stages it
# into <repo>/.opencode/tool/ at runtime; its callback wiring (URL/token/session
# id) comes from env vars the server manager injects.
ASK_AGENT_TOOL_TS = (Path(__file__).resolve().parent / "tools" / "ask_agent.ts").read_text()
ASK_TEAM_TOOL_TS = (Path(__file__).resolve().parent / "tools" / "ask_team.ts").read_text()

# Custom tools staged into <repo>/.opencode/tool/ at runtime (name -> source).
OPENCODE_TOOLS = {"ask_agent.ts": ASK_AGENT_TOOL_TS, "ask_team.ts": ASK_TEAM_TOOL_TS}


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


def _request_timeout_opts() -> dict:
    """Per-request HTTP timeouts OpenCode wires into the provider fetch (resolveSDK
    reads options.timeout/headerTimeout/chunkTimeout). Without these the model
    fetch has NO deadline, so a stuck / no-progress DeepSeek call hangs the run
    until the (long) run budget — the silent-900s hang. ``headerTimeout`` bounds
    time-to-first-response; ``chunkTimeout`` bounds a mid-stream stall (catches a
    hung stream without capping a legitimately long-but-progressing reasoning
    response); ``timeout`` is a generous hard total cap. All env-tunable in ms;
    set an env to 0 to disable that one."""
    opts: dict = {}
    for key, env, default in (
        ("timeout", "AGENT_GRAPHS_OPENCODE_REQUEST_TIMEOUT_MS", "1800000"),   # 30 min hard cap / request
        ("headerTimeout", "AGENT_GRAPHS_OPENCODE_HEADER_TIMEOUT_MS", "120000"),  # 2 min to first byte
        ("chunkTimeout", "AGENT_GRAPHS_OPENCODE_CHUNK_TIMEOUT_MS", "180000"),    # 3 min between chunks
    ):
        try:
            val = int(os.environ.get(env, default))
        except ValueError:
            val = 0
        if val > 0:
            opts[key] = val
    return opts


def _provider_block(graph: TeamGraph) -> dict:
    """Provider config covering the model backends the team's agents use."""
    providers: dict = {}
    used: dict[str, set[str]] = {}
    for node in graph.nodes:
        backend, name = split_model_string(node.spec.model)
        used.setdefault(backend, set()).add(name)

    timeouts = _request_timeout_opts()
    if "lmstudio" in used:
        providers["lmstudio"] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": "LM Studio (local)",
            "options": {"baseURL": lmstudio_base_url(), **timeouts},
            "models": {m: {} for m in sorted(used["lmstudio"])},
        }
    if "deepseek" in used:
        # DeepSeek is a built-in OpenCode provider (models.dev); we inject the
        # key and DECLARE the models we use so OpenCode's registry knows them.
        # We ALSO set per-request timeouts (above) — without them a stuck DeepSeek
        # call has no deadline and hangs the whole run. Omit the block entirely if
        # unconfigured (OpenCode errors clearly on use rather than us shipping an
        # empty key).
        key = deepseek_api_key()
        if key:
            providers["deepseek"] = {
                "options": {"apiKey": key, **timeouts},
                "models": {m: {} for m in sorted(used["deepseek"])},
            }
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
