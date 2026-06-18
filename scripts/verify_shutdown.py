"""Regression guard: the backend must shut down PROMPTLY on SIGINT even with an
open SSE /events stream — the "hangs on Waiting for connections to close" bug
that kept coming back whenever a launcher forgot uvicorn's timeout_graceful_
shutdown (its default is None = wait forever).

This launches the backend WITHOUT that flag on purpose, so it proves the
APP-level fix (`backend.main._install_sse_shutdown`: a signal handler that closes
the SSE buses the instant a termination signal lands) bounds shutdown on its own.
Covers native and (if the `opencode` binary is present) a live opencode session
whose server subprocess must also be torn down. No model / LLM needed.

Run: ./.venv/bin/python scripts/verify_shutdown.py
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

DB = "/tmp/ag_verify_shutdown.sqlite"
REPO = "/tmp/ag_verify_shutdown_repo"
PORT = 8019
BOUND_S = 8.0  # generous; the fix lands sub-second, an unbounded hang is ∞


def _free_db():
    for f in (DB, DB + "-wal", DB + "-shm"):
        try:
            os.remove(f)
        except OSError:
            pass


def _post(base, path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    return json.load(urllib.request.urlopen(req))


def run_case(harness: str) -> str | None:
    """Launch (no graceful-timeout flag), open an SSE stream, SIGINT, time exit.
    Returns a failure string or None on success."""
    _free_db()
    os.makedirs(REPO, exist_ok=True)
    base = f"http://127.0.0.1:{PORT}"
    # Deliberately NO timeout_graceful_shutdown — the app must self-bound.
    launcher = (
        "import uvicorn\n"
        "from backend.main import create_app\n"
        f"uvicorn.run(create_app(db_path={DB!r}), host='127.0.0.1', port={PORT})\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", launcher])
    try:
        for _ in range(80):
            try:
                if json.load(urllib.request.urlopen(base + "/health"))["status"] == "ok":
                    break
            except Exception:
                time.sleep(0.5)
        else:
            return f"[{harness}] backend never came up"

        team = _post(base, "/api/teams", {"name": "shutdown-probe"})
        sess = _post(base, "/api/sessions",
                     {"team_id": team["id"], "repo_path": REPO, "mode": "parallel", "harness": harness})
        sid = sess["id"]

        if harness == "opencode":
            # spawn the real server subprocess + listener so teardown is exercised
            try:
                _post(base, f"/api/agent/lead/run?session_id={sid}", {"prompt": "hi"})
            except Exception:
                pass
            time.sleep(5)

        def hold_sse():
            try:
                r = urllib.request.urlopen(base + f"/events?session_id={sid}", timeout=30)
                for _line in r:
                    pass
            except Exception:
                pass

        threading.Thread(target=hold_sse, daemon=True).start()
        time.sleep(1.5)  # let the SSE subscriber register

        t0 = time.time()
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=BOUND_S)
        except subprocess.TimeoutExpired:
            return f"[{harness}] HUNG > {BOUND_S}s on shutdown with an open SSE stream"
        dt = time.time() - t0
        print(f"[{harness}] shut down in {dt:.2f}s")
        if harness == "opencode" and shutil.which("pgrep"):
            leftover = subprocess.run(["pgrep", "-f", "opencode serve"],
                                      capture_output=True, text=True).stdout.split()
            if leftover:
                return f"[{harness}] {len(leftover)} opencode server(s) leaked after shutdown"
        return None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def main() -> int:
    cases = ["native"]
    if shutil.which("opencode"):
        cases.append("opencode")
    else:
        print("(opencode binary absent — skipping the opencode teardown case)")
    failures = [f for f in (run_case(h) for h in cases) if f]
    _free_db()
    if failures:
        print("FAILURES:\n  - " + "\n  - ".join(failures))
        return 1
    print("OK — shutdown is bounded for:", ", ".join(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
