"""A backend whose agents run a scripted FunctionModel instead of a real LLM.

Dev tooling for browser-verifying agent *flows* (ask_user cards, todos,
transcripts) deterministically, without LM Studio. The script: the agent asks
the user two questions via ask_user, then finishes with a text answer.

Run from the repo root (fresh DB every launch):

    ./.venv/bin/python scripts/scripted_backend.py [port=8001]

Then point Vite at it: AG_BACKEND=http://127.0.0.1:8001 npm run dev -- --port 5174
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn
from pydantic_ai.messages import TextPart, ToolCallPart

import backend.wiring as wiring
from tests.conftest import make_sequence_model

DB = "/tmp/ag_scripted.sqlite"


def scripted_model(_model_str: str):
    return make_sequence_model([
        [ToolCallPart("ask_user", {"questions": [
            {"question": "How many letters should the secret word have?", "options": ["5", "6", "7"]},
            {"question": "How many attempts does the player get?", "options": ["6 attempts", "8 attempts"]},
        ]})],
        [TextPart("Great — building the game with your choices now. Done!")],
    ])


if __name__ == "__main__":
    if os.path.exists(DB):
        os.remove(DB)
    wiring.resolve_model = scripted_model

    from backend.main import create_app

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    # timeout_graceful_shutdown is defense-in-depth; the app also self-bounds SSE
    # on a signal (backend.main._install_sse_shutdown), so an open /events stream
    # can't wedge shutdown here either.
    uvicorn.run(create_app(db_path=DB), port=port, log_level="warning", timeout_graceful_shutdown=3)
