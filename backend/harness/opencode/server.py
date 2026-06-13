"""Spawn and own one headless ``opencode serve`` process per session.

Design choice — DON'T litter the user's repo: the server's cwd is a dedicated
**config home** (a temp dir we own) holding the generated ``opencode.json`` +
``.opencode/tool/ask_agent.ts``. Agent *sessions* are then scoped to the user's
repo via ``POST /session?directory=<repo_root>`` (the server cwd, where config
and custom tools live, is separate from a session's working directory). So our
generated agents + delegation tool are global to this server while file
operations happen in the repo.

The server reads its callback wiring (where to POST ask_agent, the shared
token, our session id) from env vars injected here; ``ask_agent.ts`` reads them
at tool-call time.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import tempfile
from pathlib import Path

import httpx

from ...domain.models import TeamGraph
from .config import ASK_AGENT_TOOL_TS, build_opencode_config


def opencode_binary() -> str:
    return os.environ.get("AGENT_GRAPHS_OPENCODE_BIN", "opencode")


def callback_base_url() -> str:
    """Where the ask_agent custom tool POSTs back into our backend."""
    return os.environ.get("AGENT_GRAPHS_CALLBACK_URL", "http://127.0.0.1:8000")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


class OpenCodeServer:
    """A running ``opencode serve`` bound to one agent-graphs session."""

    def __init__(
        self,
        *,
        session_id: str,
        repo_root: Path,
        graph: TeamGraph,
        callback_token: str,
        binary: str | None = None,
        callback_url: str | None = None,
    ):
        self.session_id = session_id
        self.repo_root = Path(repo_root)
        self.graph = graph
        self._binary = binary or opencode_binary()
        self._callback_url = callback_url or callback_base_url()
        self._callback_token = callback_token
        self._config_home: Path | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self.base_url: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def write_config(self, graph: TeamGraph) -> Path:
        """(Re)write opencode.json + the ask_agent tool into the config home.
        Returns the config home dir. Called on start and on graph changes."""
        if self._config_home is None:
            self._config_home = Path(tempfile.mkdtemp(prefix=f"ag_oc_{self.session_id}_"))
        home = self._config_home
        (home / ".opencode" / "tool").mkdir(parents=True, exist_ok=True)
        config = build_opencode_config(graph, repo_root=self.repo_root)
        (home / "opencode.json").write_text(json.dumps(config, indent=2))
        (home / ".opencode" / "tool" / "ask_agent.ts").write_text(ASK_AGENT_TOOL_TS)
        self.graph = graph
        return home

    def _env(self) -> dict:
        env = dict(os.environ)
        env["AGENT_GRAPHS_CALLBACK_URL"] = self._callback_url
        env["AGENT_GRAPHS_CALLBACK_TOKEN"] = self._callback_token
        env["AGENT_GRAPHS_SESSION_ID"] = self.session_id
        # Don't let the server pick up the user's own global opencode config /
        # auto-update chatter; our config home is authoritative for this run.
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
        return env

    async def start(self, *, timeout: float = 30.0) -> None:
        """Boot the server (idempotent). Writes config, spawns the process, and
        waits until ``/config`` answers."""
        async with self._lock:
            if self.running:
                return
            home = self.write_config(self.graph)
            self.port = _free_port()
            self.base_url = f"http://127.0.0.1:{self.port}"
            log = open(home / "serve.log", "w")  # noqa: SIM115 — closed on shutdown
            self._log_file = log
            self._proc = await asyncio.create_subprocess_exec(
                self._binary, "serve", "--port", str(self.port), "--hostname", "127.0.0.1",
                cwd=str(home),
                env=self._env(),
                stdout=log,
                stderr=log,
            )
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
            await self._await_ready(timeout)

    async def _await_ready(self, timeout: float) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        last_err: Exception | None = None
        while asyncio.get_event_loop().time() < deadline:
            if not self.running:
                raise RuntimeError(f"opencode server exited early (see {self._config_home}/serve.log)")
            try:
                r = await self._client.get("/config")
                if r.status_code == 200:
                    return
            except Exception as e:  # noqa: BLE001 — not up yet
                last_err = e
            await asyncio.sleep(0.25)
        raise TimeoutError(f"opencode server not ready in {timeout}s ({last_err})")

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("server not started")
        return self._client

    async def reconfigure(self, graph: TeamGraph) -> None:
        """Apply a graph/spec change: rewrite config and restart the server
        (OpenCode reads config at boot). Sessions are recreated lazily after."""
        await self.shutdown(cleanup=False)
        self.write_config(graph)
        await self.start()

    async def shutdown(self, *, cleanup: bool = True) -> None:
        """Terminate the process; optionally remove the config home."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if getattr(self, "_log_file", None) is not None:
            self._log_file.close()
            self._log_file = None
        if cleanup and self._config_home is not None:
            shutil.rmtree(self._config_home, ignore_errors=True)
            self._config_home = None
