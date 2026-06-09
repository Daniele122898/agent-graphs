"""Tiny shared helpers: id generation and a clock.

Both are injectable so tests can use a fake clock / deterministic ids. The
defaults are the only place ``uuid`` and wall-clock time are read, keeping the
rest of the code pure and testable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id(prefix: str = "") -> str:
    """A short unique id, optionally prefixed (e.g. ``team_``, ``sess_``)."""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def iso_now() -> str:
    """Current UTC time as an ISO-8601 string (the app's timestamp format)."""
    return datetime.now(timezone.utc).isoformat()
