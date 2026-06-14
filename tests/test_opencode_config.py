"""Pure config generation: TeamGraph -> opencode.json. No server, no LLM."""

from __future__ import annotations

from pathlib import Path

from backend.domain.models import AgentSpec, Capabilities, GraphEdge, GraphNode, TeamGraph
from backend.harness.opencode.config import (
    ASK_AGENT_TOOL_TS,
    build_opencode_config,
    capability_permission,
    opencode_model_id,
)
from backend.harness.opencode.prompt import build_opencode_prompt


def _graph() -> TeamGraph:
    lead = AgentSpec(
        id="lead", name="Lead", persona="You are the lead.", is_entry_point=True,
        model="lmstudio:qwen/qwen3.5-9b", capabilities=Capabilities.from_level("read-write"),
    )
    expert = AgentSpec(
        id="expert", name="Py Expert", persona="You know Python.",
        model="deepseek:deepseek-v4-flash", capabilities=Capabilities.from_level("read", bash=False),
    )
    return TeamGraph(
        nodes=[GraphNode(spec=lead), GraphNode(spec=expert)],
        edges=[GraphEdge(id="e1", source="lead", target="expert", label="Python questions")],
    )


def test_model_id_translation():
    assert opencode_model_id("lmstudio:qwen/qwen3.5-9b") == "lmstudio/qwen/qwen3.5-9b"
    assert opencode_model_id("deepseek:deepseek-v4-flash") == "deepseek/deepseek-v4-flash"
    assert opencode_model_id("local:m") == "lmstudio/m"  # alias resolved


def test_permission_mapping_full_access():
    perm = capability_permission(Capabilities.from_level("read-write"))
    assert perm["read"] == "allow"
    assert perm["edit"] == "allow"
    assert perm["bash"] == "allow"
    # the non-negotiables for parity:
    assert perm["question"] == "allow"  # else ask_user never fires
    assert perm["task"] == "deny"       # we use ask_agent, not opencode subagents
    assert perm["webfetch"] == "deny" and perm["websearch"] == "deny"


def test_permission_mapping_read_only_no_bash():
    perm = capability_permission(Capabilities.from_level("read", bash=False))
    assert perm["read"] == "allow"
    assert perm["edit"] == "deny"
    assert perm["bash"] == "deny"


def test_permission_mapping_path_globs():
    caps = Capabilities(filesystem="read-write", read_paths=["src/**", "docs/**"], write_paths=["src/**"])
    perm = capability_permission(caps)
    assert perm["read"] == {"src/**": "allow", "docs/**": "allow", "*": "deny"}
    assert perm["edit"] == {"src/**": "allow", "*": "deny"}


def test_permission_mapping_no_access():
    perm = capability_permission(Capabilities.from_level("none", bash=False))
    assert perm["read"] == "deny" and perm["edit"] == "deny" and perm["bash"] == "deny"


def test_build_config_shape():
    cfg = build_opencode_config(_graph(), repo_root=Path("/tmp/repo"))
    assert cfg["$schema"].startswith("https://opencode.ai")
    # one opencode agent per spec, keyed by our agent id
    assert set(cfg["agent"]) == {"lead", "expert"}
    lead = cfg["agent"]["lead"]
    assert lead["model"] == "lmstudio/qwen/qwen3.5-9b"
    assert lead["mode"] == "primary"
    assert lead["tools"] == {"task": False, "webfetch": False, "websearch": False}
    assert lead["permission"]["question"] == "allow"
    # the lead's prompt carries persona + the neighbor (expert) + opencode tool guidance
    assert "You are the lead." in lead["prompt"]
    assert "ask_agent" in lead["prompt"]
    assert "Py Expert" in lead["prompt"] and "expert" in lead["prompt"]
    assert "question` tool" in lead["prompt"]  # ask_user guidance, opencode-flavored
    # NOT the native edit-token guidance
    assert "edit-token" not in lead["prompt"]


def test_provider_block_lmstudio_always_deepseek_when_keyed(monkeypatch, tmp_path):
    # no deepseek key configured -> deepseek omitted even though an agent uses it
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_GRAPHS_CONFIG", str(tmp_path / "absent.yml"))
    monkeypatch.setenv("AGENT_GRAPHS_LMSTUDIO_URL", "http://127.0.0.1:1234/v1")
    cfg = build_opencode_config(_graph(), repo_root=Path("/tmp/repo"))
    assert "lmstudio" in cfg["provider"]
    assert cfg["provider"]["lmstudio"]["options"]["baseURL"] == "http://127.0.0.1:1234/v1"
    assert cfg["provider"]["lmstudio"]["models"] == {"qwen/qwen3.5-9b": {}}
    assert "deepseek" not in cfg["provider"]  # no key -> omitted

    # with a key, deepseek appears
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-real")
    cfg2 = build_opencode_config(_graph(), repo_root=Path("/tmp/repo"))
    assert cfg2["provider"]["deepseek"]["options"]["apiKey"] == "sk-test-not-real"


def test_provider_block_sets_per_request_timeouts(monkeypatch, tmp_path):
    # Per-request timeouts must be wired into BOTH provider blocks so a stuck
    # model fetch fails fast instead of hanging the run (the silent-900s hang).
    monkeypatch.setenv("AGENT_GRAPHS_CONFIG", str(tmp_path / "absent.yml"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("AGENT_GRAPHS_OPENCODE_REQUEST_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("AGENT_GRAPHS_OPENCODE_HEADER_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("AGENT_GRAPHS_OPENCODE_CHUNK_TIMEOUT_MS", raising=False)
    cfg = build_opencode_config(_graph(), repo_root=Path("/tmp/repo"))
    for prov in ("lmstudio", "deepseek"):
        opts = cfg["provider"][prov]["options"]
        assert opts["timeout"] > 0 and opts["headerTimeout"] > 0 and opts["chunkTimeout"] > 0
    # an env set to 0 disables that one
    monkeypatch.setenv("AGENT_GRAPHS_OPENCODE_CHUNK_TIMEOUT_MS", "0")
    cfg2 = build_opencode_config(_graph(), repo_root=Path("/tmp/repo"))
    assert "chunkTimeout" not in cfg2["provider"]["deepseek"]["options"]


def test_ask_agent_tool_uses_env_wiring():
    # the custom tool must read its callback wiring from env (server manager
    # injects it) and pass ctx.agent as the asker
    assert "AGENT_GRAPHS_CALLBACK_URL" in ASK_AGENT_TOOL_TS
    assert "AGENT_GRAPHS_SESSION_ID" in ASK_AGENT_TOOL_TS
    assert "asker_id: ctx.agent" in ASK_AGENT_TOOL_TS
    assert "/internal/ask_agent" in ASK_AGENT_TOOL_TS


def test_prompt_omits_neighbor_section_when_no_edges():
    solo = TeamGraph(nodes=[GraphNode(spec=AgentSpec(id="solo", name="Solo", model="lmstudio:m"))])
    p = build_opencode_prompt(solo.nodes[0].spec, solo, Path("/tmp/r"))
    assert "consult" not in p.lower() or "ask_agent" in p  # tool guidance still present
    assert "Solo" in p
