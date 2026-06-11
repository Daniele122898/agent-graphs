"""DeepSeek — the hosted DeepSeek API (OpenAI-compatible).

Driven through pydantic-ai's own ``DeepSeekProvider``, whose model profile
already handles the API's quirks: reasoning arrives in ``reasoning_content``,
and (since DeepSeek v3.2) prior-turn thinking MUST be sent back in tool-call
loops (the profile sets ``openai_chat_send_back_thinking_parts='field'`` —
omitting it is a 400 from the API). Do not "optimize" that away like we do for
LM Studio; the two APIs want opposite behavior.

Thinking control (current API, v4 models): a request-body ``thinking`` object —
``{"type": "enabled"|"disabled", "reasoning_effort": "high"|"max"}`` — sent via
``extra_body`` (it is not a standard OpenAI parameter). Selecting thinking by
model id (deepseek-chat vs deepseek-reasoner) is the legacy mechanism,
deprecated 2026-07-24; we use the parameter exclusively. Default when nothing
is chosen: the API's own default (thinking enabled, effort high).
"""

from __future__ import annotations

import httpx
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.settings import ModelSettings

from ..config import provider_setting
from .base import ModelBackend, ThinkingSupport

DEEPSEEK_API_BASE = "https://api.deepseek.com"

CONFIG_HINT = "set providers.deepseek.api_key in config.yml (or the DEEPSEEK_API_KEY env var)"


def deepseek_api_key() -> str | None:
    return provider_setting("deepseek", "api_key", env="DEEPSEEK_API_KEY")


class DeepSeekBackend(ModelBackend):
    id = "deepseek"
    label = "DeepSeek API"
    default_model = "deepseek-v4-flash"
    # The API accepts exactly these efforts (low/medium map to high, xhigh to max).
    thinking = ThinkingSupport(toggleable=True, efforts=("high", "max"))

    def configured(self) -> str | None:
        return None if deepseek_api_key() else CONFIG_HINT

    async def list_models(self) -> list[dict]:
        key = deepseek_api_key()
        if not key:
            raise RuntimeError(CONFIG_HINT)
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{DEEPSEEK_API_BASE}/models", headers={"Authorization": f"Bearer {key}"}
            )
            r.raise_for_status()
            rows = r.json().get("data", [])
        # All current DeepSeek chat models support function calling.
        return [{"id": m["id"], "label": m["id"], "tool_use": True} for m in rows]

    def build(self, name: str) -> Model:
        key = deepseek_api_key()
        if not key:
            raise RuntimeError(f"DeepSeek is not configured: {CONFIG_HINT}")
        return OpenAIChatModel(name, provider=DeepSeekProvider(api_key=key))

    def thinking_settings(self, thinking: bool | None, effort: str | None) -> ModelSettings | None:
        if thinking is None and not effort:
            return None  # let the API default (enabled, high) apply
        if thinking is False:
            body = {"type": "disabled"}
        else:  # explicitly on, or an effort choice implies thinking on
            body = {"type": "enabled"}
            if effort:
                body["reasoning_effort"] = effort
        return OpenAIChatModelSettings(extra_body={"thinking": body})
