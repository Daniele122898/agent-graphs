"""The backend registry + per-agent model resolution.

A model is just config: the string ``"<backend-id>:<model-name>"`` (e.g.
``lmstudio:qwen/qwen3.5-9b``, ``deepseek:deepseek-v4-flash``) resolves through
the matching ``ModelBackend`` to a Pydantic AI model instance. The result is
**injected** into the agent (never constructed inside it), which is what lets
tests swap in ``FunctionModel`` for deterministic, token-free runs.

Adding a backend = subclass ``ModelBackend`` + add an instance to ``BACKENDS``.
Strings with an unknown prefix fall through to Pydantic AI's own inference
(``openai:gpt-4o``, ``anthropic:...`` if that extra is installed), so
hand-typed specs keep working without a registered backend.

``thinking_settings`` is the parallel dispatch for an agent's thinking
preference: it maps (model string, thinking on/off, effort) to backend-specific
``ModelSettings`` — separate from ``resolve_model`` so the test seam (resolver
takes a string, returns a Model) stays untouched.
"""

from __future__ import annotations

from pydantic_ai.models import Model, infer_model
from pydantic_ai.settings import ModelSettings

from .base import ModelBackend
from .deepseek import DeepSeekBackend
from .lmstudio import LMStudioBackend

BACKENDS: dict[str, ModelBackend] = {b.id: b for b in (LMStudioBackend(), DeepSeekBackend())}

_ALIASES = {"local": "lmstudio"}


def split_model_string(model_str: str) -> tuple[str, str]:
    """``"lmstudio:qwen/x"`` → ``("lmstudio", "qwen/x")`` (alias-resolved);
    a bare name gets an empty backend id."""
    if ":" not in model_str:
        return "", model_str
    prefix, name = model_str.split(":", 1)
    return _ALIASES.get(prefix, prefix), name


def backend_for(model_str: str) -> ModelBackend | None:
    backend_id, _ = split_model_string(model_str)
    return BACKENDS.get(backend_id)


def resolve_model(model_str: str) -> Model:
    """Resolve a per-agent model string to a Pydantic AI model instance."""
    backend = backend_for(model_str)
    if backend is not None:
        return backend.build(split_model_string(model_str)[1])
    # Defer to Pydantic AI for anything else (openai:, anthropic:, google:, ...).
    return infer_model(model_str)


def thinking_settings(
    model_str: str, thinking: bool | None, thinking_effort: str | None
) -> ModelSettings | None:
    """Backend-specific request settings for an agent's thinking preference,
    or None when the backend has no thinking controls / nothing was chosen."""
    backend = backend_for(model_str)
    if backend is None:
        return None
    return backend.thinking_settings(thinking, thinking_effort)
