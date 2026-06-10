"""Per-agent model resolution — provider-agnostic.

A model is just config. A model string like ``lmstudio:qwen2.5-coder-7b`` or
``openai:gpt-4o`` resolves to a Pydantic AI model instance. The result is
**injected** into the agent (never constructed inside it), which is what lets
tests swap in ``FunctionModel`` for deterministic, token-free runs.

Supported prefixes:

- ``lmstudio:<name>`` / ``local:<name>`` — an OpenAI-compatible endpoint
  (LM Studio at ``AGENT_GRAPHS_LMSTUDIO_URL``, default ``http://127.0.0.1:1234/v1``).
- ``openai:<name>`` — OpenAI proper (uses ``OPENAI_API_KEY``).
- anything else — handed to Pydantic AI's own inference (e.g. ``anthropic:...``
  if that provider extra is installed), so adding providers is a pip install.
"""

from __future__ import annotations

import dataclasses
import os

import httpx
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile, openai_model_profile
from pydantic_ai.providers.openai import OpenAIProvider

# A local model that stops responding (e.g. it was swapped/unloaded mid-call)
# must fail the run, not hang it forever. Generation on a weak laptop is slow,
# so the read timeout is generous — but finite.
LOCAL_READ_TIMEOUT = float(os.environ.get("AGENT_GRAPHS_LOCAL_READ_TIMEOUT", "600"))


def lmstudio_base_url() -> str:
    return os.environ.get("AGENT_GRAPHS_LMSTUDIO_URL", "http://127.0.0.1:1234/v1")


def resolve_model(model_str: str) -> Model:
    """Resolve a per-agent model string to a Pydantic AI model instance."""
    if ":" not in model_str:
        return infer_model(model_str)

    prefix, name = model_str.split(":", 1)
    if prefix in ("lmstudio", "local"):
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=LOCAL_READ_TIMEOUT, write=60, pool=60)
        )
        provider = OpenAIProvider(base_url=lmstudio_base_url(), api_key="lm-studio", http_client=http_client)
        # Local thinking models (qwen3.5, ...) return reasoning in a
        # `reasoning_content` field; pydantic-ai's default ('auto') echoes it
        # back into every subsequent request, re-feeding the whole reasoning
        # trace to a small model on a weak machine. Qwen's own guidance is to
        # drop prior-turn thinking, so never send it back.
        profile = dataclasses.replace(
            OpenAIModelProfile.from_profile(openai_model_profile(name)),
            openai_chat_send_back_thinking_parts=False,
        )
        return OpenAIChatModel(name, provider=provider, profile=profile)
    if prefix == "openai":
        return OpenAIChatModel(name)
    # Defer to Pydantic AI for any other provider (anthropic:, google:, ...).
    return infer_model(model_str)
