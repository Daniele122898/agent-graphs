"""Model-backend endpoints: which backends exist, and what models each offers.

Drives the Capabilities tab's backend dropdown (above the model dropdown) and
its thinking controls. Listing failures (no key, server down) come back as a
friendly ``{models: [], error}`` payload, never a 500 — the UI must degrade
gracefully."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from ..providers.registry import BACKENDS


def install(app: FastAPI) -> None:
    @app.get("/api/providers")
    def list_providers() -> dict:
        """Every registered model backend with its UI metadata (configured?,
        default model, thinking support)."""
        return {"providers": [b.info().payload() for b in BACKENDS.values()]}

    @app.get("/api/providers/{provider_id}/models")
    async def provider_models(provider_id: str) -> dict:
        backend = BACKENDS.get(provider_id)
        if backend is None:
            raise HTTPException(404, f"no model backend '{provider_id}'")
        try:
            return {"models": await backend.list_models(), "error": None}
        except Exception as e:  # noqa: BLE001 — degrade gracefully in the UI
            return {"models": [], "error": str(e)}
