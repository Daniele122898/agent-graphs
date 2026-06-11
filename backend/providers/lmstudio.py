"""LM Studio — a local OpenAI-compatible server (the default model backend).

Two surfaces, one host:

- ``/v1`` — the OpenAI-compatible API the models are *driven* through.
- ``/api/v0`` — LM Studio's richer native REST API, used read-only for model
  stats: quantization, ``max_context_length`` vs ``loaded_context_length`` (the
  documented quirk where the loaded value is often a small default — worth
  flagging in the UI), ``state`` (loaded/not-loaded), and ``capabilities``
  (e.g. ``tool_use`` — REQUIRED for agents; without it tool calls come back as
  text and silently do nothing).
"""

from __future__ import annotations

import dataclasses
import os

import httpx
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile, openai_model_profile
from pydantic_ai.providers.openai import OpenAIProvider

# A local model that stops responding (e.g. it was swapped/unloaded mid-call)
# must fail the run, not hang it forever. Generation on a weak laptop is slow,
# so the read timeout is generous — but finite.
LOCAL_READ_TIMEOUT = float(os.environ.get("AGENT_GRAPHS_LOCAL_READ_TIMEOUT", "600"))


def lmstudio_base_url() -> str:
    return os.environ.get("AGENT_GRAPHS_LMSTUDIO_URL", "http://127.0.0.1:1234/v1")


def build_lmstudio_model(name: str) -> Model:
    """An LM Studio-served model, driven through its OpenAI-compatible API."""
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


def _lmstudio_root() -> str:
    """The LM Studio host root (the REST API lives at ``/api/v0``, not ``/v1``)."""
    base = lmstudio_base_url()
    return base[: -len("/v1")] if base.endswith("/v1") else base.rstrip("/")


async def lmstudio_models() -> list[dict]:
    """Fetch rich model stats from LM Studio. Raises on connection error; the
    endpoint wrapper turns that into a friendly payload."""
    url = f"{_lmstudio_root()}/api/v0/models"
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json().get("data", [])
