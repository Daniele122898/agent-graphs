"""The model-backend abstraction — one class per API a model can live behind.

A **backend** (LM Studio, DeepSeek, ...) knows four things: whether it is
configured (keys present), which models it offers, how to build a Pydantic AI
``Model`` for one of them, and what *thinking* controls it supports. Everything
else in the app talks to backends only through this interface + the registry,
so adding a new API is: subclass ``ModelBackend``, instantiate it in
``registry.BACKENDS``, done — the provider dropdown, model picker and thinking
controls in the UI light up from the metadata here.

Model strings stay ``"<backend-id>:<model-name>"`` everywhere (persisted specs,
the wire, the UI), so adding backends never migrates data.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings


@dataclass(frozen=True)
class ThinkingSupport:
    """What thinking/reasoning controls a backend exposes. Drives the UI."""

    toggleable: bool = False
    """Can thinking be switched on/off per agent?"""
    efforts: tuple[str, ...] = ()
    """Allowed effort levels while thinking (empty = no effort control)."""


@dataclass(frozen=True)
class BackendInfo:
    """Serializable description of a backend for the UI."""

    id: str
    label: str
    default_model: str
    configured: bool
    hint: str = ""
    thinking: ThinkingSupport = field(default_factory=ThinkingSupport)

    def payload(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "default_model": self.default_model,
            "configured": self.configured,
            "hint": self.hint,
            "thinking": {
                "toggleable": self.thinking.toggleable,
                "efforts": list(self.thinking.efforts),
            },
        }


class ModelBackend(ABC):
    """One model API. Stateless; all credentials/endpoints are read from
    config at call time (see backend/config.py) so edits apply immediately."""

    id: str
    label: str
    default_model: str
    thinking: ThinkingSupport = ThinkingSupport()

    def configured(self) -> str | None:
        """None when ready to use; otherwise a human hint about what's missing
        (e.g. "set providers.deepseek.api_key in config.yml")."""
        return None

    @abstractmethod
    async def list_models(self) -> list[dict]:
        """Available models, normalized to ``{id, label, tool_use}`` rows.
        ``tool_use`` is True/False when the backend knows whether the model can
        function-call (agents are useless without it), None when unknown.
        Raises on connection/auth errors — the endpoint wraps that into a
        friendly payload."""

    @abstractmethod
    def build(self, name: str) -> Model:
        """A Pydantic AI model instance for ``name`` on this backend."""

    def thinking_settings(self, thinking: bool | None, effort: str | None) -> ModelSettings | None:
        """Backend-specific request settings for a thinking preference.
        ``thinking=None`` means "backend default". Base: no thinking controls."""
        return None

    def info(self) -> BackendInfo:
        hint = self.configured()
        return BackendInfo(
            id=self.id,
            label=self.label,
            default_model=self.default_model,
            configured=hint is None,
            hint=hint or "",
            thinking=self.thinking,
        )
