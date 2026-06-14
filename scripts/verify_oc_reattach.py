"""Focused live check of the Step-6 reattach assumption: does a RE-SPAWNED real
opencode server still resolve a session id created by a previous process (same
repo)? If yes, persisting oc_session_id is enough to restore the transcript after
a restart. No model needed — we only test session persistence across a respawn.

Uses our real OpenCodeServer/OpenCodeClient on an isolated temp repo; does NOT
touch the user's db.sqlite or their running dev servers."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from backend.domain.models import AgentSpec, Capabilities, GraphNode, TeamGraph
from backend.harness.opencode.harness import Connection
from backend.harness.opencode.server import OpenCodeServer


def _graph() -> TeamGraph:
    return TeamGraph(nodes=[GraphNode(spec=AgentSpec(
        id="lead", name="Lead", is_entry_point=True, model="lmstudio:qwen/qwen3.5-9b",
        capabilities=Capabilities.from_level("read-write")))])


async def main() -> None:
    repo = Path(tempfile.mkdtemp(prefix="ag_reattach_repo_"))
    graph = _graph()

    print(f"repo: {repo}")
    # --- process 1: create a session, then shut the server down ----------------
    srv1 = OpenCodeServer(session_id="s1", repo_root=repo, graph=graph, callback_token="t")
    conn1 = Connection(srv1)
    await conn1.start()
    sid = await conn1.client.create_session(agent="lead", directory=str(repo))
    print(f"process 1: created OC session {sid}")
    msgs1 = await conn1.client.messages(sid)
    print(f"process 1: messages -> {len(msgs1)} (empty session ok)")
    # shut down WITHOUT cleanup so .opencode survives (OpenCode persists sessions
    # to its own on-disk store; we only need the repo cwd to match on respawn).
    await srv1.shutdown(cleanup=False)
    print("process 1: server shut down")

    # --- process 2: re-spawn on the SAME repo, try to resolve the old session --
    srv2 = OpenCodeServer(session_id="s1", repo_root=repo, graph=graph, callback_token="t")
    conn2 = Connection(srv2)
    await conn2.start()
    try:
        msgs2 = await conn2.client.messages(sid)
        print(f"process 2: RESOLVED old session {sid} -> {len(msgs2)} messages")
        ok = True
    except Exception as e:  # noqa: BLE001
        print(f"process 2: FAILED to resolve old session {sid}: {e!r}")
        ok = False
    # also list sessions to see what the respawned server knows
    try:
        r = await conn2.client._http.get("/session", params={"directory": str(repo)})
        sessions = r.json() if r.status_code == 200 else []
        ids = [s.get("id") for s in (sessions if isinstance(sessions, list) else sessions.get("data", []))]
        print(f"process 2: /session lists {len(ids)} session(s); old id present: {sid in ids}")
    except Exception as e:  # noqa: BLE001
        print(f"process 2: /session list failed: {e!r}")
    await srv2.shutdown(cleanup=True)

    print("\nRESULT:", "REATTACH WORKS ✅" if ok else "REATTACH BROKEN ❌ (need DB mirror fallback)")


if __name__ == "__main__":
    asyncio.run(main())
