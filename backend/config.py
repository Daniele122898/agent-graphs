"""User-local configuration — ``config.yml`` at the repo root.

API keys and provider endpoints must never be committed: ``config.yml`` is
gitignored and holds the real values; ``config.example.yml`` (committed) shows
the shape. Precedence per setting: **environment variable > config.yml >
built-in default**, so existing env-based workflows keep working and CI/tests
can override without touching files.

The file is re-read on every access (it is tiny and read on config-time paths
only — provider listing, agent build — never per token), so edits apply
without a backend restart.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# Repo root (backend/config.py -> backend/ -> root). Overridable for tests.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yml"


def config_path() -> Path:
    return Path(os.environ.get("AGENT_GRAPHS_CONFIG", str(DEFAULT_CONFIG_PATH)))


def load_config() -> dict:
    """The parsed config.yml, or {} if absent/empty/unreadable. Never raises —
    a broken local config must not take the whole backend down."""
    try:
        raw = config_path().read_text()
    except OSError:
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def provider_setting(provider: str, key: str, *, env: str | None = None, default: Any = None) -> Any:
    """One provider setting with the env > config.yml > default precedence.

    ``env`` names the overriding environment variable (e.g. DEEPSEEK_API_KEY).
    """
    if env:
        val = os.environ.get(env)
        if val:
            return val
    providers = load_config().get("providers")
    if isinstance(providers, dict):
        section = providers.get(provider)
        if isinstance(section, dict) and section.get(key) not in (None, ""):
            return section[key]
    return default
