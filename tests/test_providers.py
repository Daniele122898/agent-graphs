"""The model-backend abstraction: config precedence, model-string resolution,
thinking-preference mapping, and the provider endpoints.

No network: DeepSeek/LM Studio listing is exercised through monkeypatched
fetchers; building models only constructs client objects."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

import backend.api.providers as providers_api
from backend.config import load_config, provider_setting
from backend.domain.models import AgentSpec
from backend.main import create_app
from backend.providers.deepseek import DeepSeekBackend
from backend.providers.lmstudio import LMStudioBackend, lmstudio_base_url
from backend.providers.registry import (
    BACKENDS,
    backend_for,
    resolve_model,
    split_model_string,
    thinking_settings,
)


# --- config.yml loading -------------------------------------------------------


def test_config_precedence_env_beats_file_beats_default(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yml"
    cfg.write_text("providers:\n  deepseek:\n    api_key: from-file\n")
    monkeypatch.setenv("AGENT_GRAPHS_CONFIG", str(cfg))

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert provider_setting("deepseek", "api_key", env="DEEPSEEK_API_KEY") == "from-file"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert provider_setting("deepseek", "api_key", env="DEEPSEEK_API_KEY") == "from-env"

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert provider_setting("deepseek", "missing", default="fallback") == "fallback"


def test_broken_or_missing_config_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_GRAPHS_CONFIG", str(tmp_path / "nope.yml"))
    assert load_config() == {}
    bad = tmp_path / "bad.yml"
    bad.write_text("providers: [unclosed")
    monkeypatch.setenv("AGENT_GRAPHS_CONFIG", str(bad))
    assert load_config() == {}


def test_lmstudio_base_url_reads_config(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_GRAPHS_LMSTUDIO_URL", raising=False)
    cfg = tmp_path / "config.yml"
    cfg.write_text("providers:\n  lmstudio:\n    base_url: http://10.0.0.5:1234/v1\n")
    monkeypatch.setenv("AGENT_GRAPHS_CONFIG", str(cfg))
    assert lmstudio_base_url() == "http://10.0.0.5:1234/v1"
    monkeypatch.setenv("AGENT_GRAPHS_LMSTUDIO_URL", "http://127.0.0.1:9999/v1")
    assert lmstudio_base_url() == "http://127.0.0.1:9999/v1"


# --- registry dispatch --------------------------------------------------------


def test_model_strings_route_to_their_backend():
    assert split_model_string("lmstudio:qwen/qwen3.5-9b") == ("lmstudio", "qwen/qwen3.5-9b")
    assert split_model_string("local:m") == ("lmstudio", "m")  # alias survives
    assert isinstance(backend_for("lmstudio:x"), LMStudioBackend)
    assert isinstance(backend_for("deepseek:deepseek-v4-flash"), DeepSeekBackend)
    assert backend_for("openai:gpt-4o") is None  # falls through to infer_model


def test_deepseek_build_uses_the_configured_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-real")
    model = resolve_model("deepseek:deepseek-v4-flash")
    assert model.model_name == "deepseek-v4-flash"
    # The pydantic-ai DeepSeek profile must keep sending thinking parts back
    # (the v4 API 400s in tool loops without them) — never disable this.
    assert model.profile.openai_chat_send_back_thinking_parts == "field"


def test_deepseek_build_without_key_fails_with_the_hint(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_GRAPHS_CONFIG", str(tmp_path / "absent.yml"))
    try:
        resolve_model("deepseek:deepseek-v4-flash")
        raise AssertionError("expected a configuration error")
    except RuntimeError as e:
        assert "config.yml" in str(e)


# --- thinking preference mapping ------------------------------------------------


def test_thinking_settings_map_to_deepseeks_native_parameter():
    # nothing chosen -> no settings (the API default applies)
    assert thinking_settings("deepseek:deepseek-v4-flash", None, None) is None
    off = thinking_settings("deepseek:deepseek-v4-flash", False, None)
    assert off["extra_body"] == {"thinking": {"type": "disabled"}}
    on = thinking_settings("deepseek:deepseek-v4-flash", True, None)
    assert on["extra_body"] == {"thinking": {"type": "enabled"}}
    effort = thinking_settings("deepseek:deepseek-v4-flash", True, "max")
    assert effort["extra_body"] == {"thinking": {"type": "enabled", "reasoning_effort": "max"}}
    # choosing an effort alone implies thinking on
    implied = thinking_settings("deepseek:deepseek-v4-flash", None, "high")
    assert implied["extra_body"]["thinking"]["type"] == "enabled"


def test_backends_without_thinking_controls_return_none():
    assert thinking_settings("lmstudio:qwen/qwen3.5-9b", True, "high") is None
    assert thinking_settings("openai:gpt-4o", True, None) is None


def test_agent_spec_thinking_fields_are_optional_and_round_trip():
    # old persisted specs (no thinking keys) must keep loading
    old = AgentSpec.model_validate({"id": "a", "name": "A"})
    assert old.thinking is None and old.thinking_effort is None
    new = AgentSpec(id="b", name="B", model="deepseek:deepseek-v4-flash", thinking=True, thinking_effort="max")
    again = AgentSpec.model_validate(new.model_dump())
    assert again.thinking is True and again.thinking_effort == "max"


# --- endpoints ------------------------------------------------------------------


def test_providers_endpoint_lists_backends_with_thinking_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_GRAPHS_CONFIG", str(tmp_path / "absent.yml"))
    with TestClient(create_app(db_path=tmp_path / "db.sqlite")) as client:
        body = client.get("/api/providers").json()
    by_id = {p["id"]: p for p in body["providers"]}
    assert by_id["lmstudio"]["thinking"] == {"toggleable": False, "efforts": []}
    ds = by_id["deepseek"]
    assert ds["thinking"] == {"toggleable": True, "efforts": ["high", "max"]}
    assert ds["configured"] is False and "config.yml" in ds["hint"]
    assert ds["default_model"] == "deepseek-v4-flash"


def test_provider_models_endpoint_normalizes_and_degrades(tmp_path, monkeypatch):
    async def fake_list(self):
        return [{"id": "deepseek-v4-flash", "label": "deepseek-v4-flash", "tool_use": True}]

    monkeypatch.setattr(DeepSeekBackend, "list_models", fake_list)

    async def broken_list(self):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(LMStudioBackend, "list_models", broken_list)

    with TestClient(create_app(db_path=tmp_path / "db.sqlite")) as client:
        ok = client.get("/api/providers/deepseek/models").json()
        assert ok["models"][0]["id"] == "deepseek-v4-flash" and ok["error"] is None
        degraded = client.get("/api/providers/lmstudio/models").json()
        assert degraded["models"] == [] and "refused" in degraded["error"]
        assert client.get("/api/providers/nope/models").status_code == 404


def test_running_agent_carries_thinking_settings_into_the_agent(tmp_path, monkeypatch):
    """The spec's thinking preference must reach the built pydantic-ai Agent —
    that's the wire between the UI fields and the actual requests."""
    from backend.providers import registry as reg

    spec = AgentSpec(
        id="x", name="X", model="deepseek:deepseek-v4-flash", thinking=True, thinking_effort="max"
    )
    settings = reg.thinking_settings(spec.model, spec.thinking, spec.thinking_effort)
    assert settings["extra_body"] == {"thinking": {"type": "enabled", "reasoning_effort": "max"}}
    # and the registry is what workers.py consults (signature lock)
    assert providers_api.BACKENDS is BACKENDS
