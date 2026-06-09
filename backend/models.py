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

import os

from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


def lmstudio_base_url() -> str:
    return os.environ.get("AGENT_GRAPHS_LMSTUDIO_URL", "http://127.0.0.1:1234/v1")


def resolve_model(model_str: str) -> Model:
    """Resolve a per-agent model string to a Pydantic AI model instance."""
    if ":" not in model_str:
        return infer_model(model_str)

    prefix, name = model_str.split(":", 1)
    if prefix in ("lmstudio", "local"):
        provider = OpenAIProvider(base_url=lmstudio_base_url(), api_key="lm-studio")
        return OpenAIChatModel(name, provider=provider)
    if prefix == "openai":
        return OpenAIChatModel(name)
    # Defer to Pydantic AI for any other provider (anthropic:, google:, ...).
    return infer_model(model_str)
