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
from .config import OPENCODE_TOOLS, build_opencode_config


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
        self._config = build_opencode_config(graph, repo_root=self.repo_root)
        self._log_dir: Path | None = None  # temp dir for serve.log (outside repo)
        # Whether <repo>/.opencode existed BEFORE us. If we create it, we own it
        # and remove it wholesale on shutdown (OpenCode installs the tool's deps
        # into .opencode/node_modules); if it pre-existed (a user's own), we
        # touch only our tool file.
        self._opencode_dir = self.repo_root / ".opencode"
        self._opencode_created = not self._opencode_dir.exists()
        self._tool_dir = self._opencode_dir / "tool"
        # Tool files WE created (only ones we wrote get cleaned up — never a
        # user's own .opencode/tool file of the same name).
        self._tools_created: list[Path] = []
        self._proc: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self.base_url: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _stage(self, graph: TeamGraph) -> None:
        """Prepare config + the custom tool.

        The server's cwd is the REPO (OpenCode's ``prompt_async`` only runs when
        the session directory matches the server's project — the config-home
        approach silently no-ops, verified against 1.16.2). So the config is
        passed inline via ``OPENCODE_CONFIG_CONTENT`` (no ``opencode.json`` file
        in the repo), and only the ask_agent tool is written into
        ``<repo>/.opencode/tool/`` — tracked and removed on shutdown. Existing
        user files are never overwritten or deleted.
        """
        self.graph = graph
        self._config = build_opencode_config(graph, repo_root=self.repo_root)
        self._tool_dir.mkdir(parents=True, exist_ok=True)
        for name, src in OPENCODE_TOOLS.items():
            f = self._tool_dir / name
            if not f.exists():
                f.write_text(src)
                self._tools_created.append(f)

    def _unstage(self) -> None:
        # Full teardown of a .opencode we created (incl. the node_modules
        # OpenCode installs for the tools); otherwise just the tool files WE wrote.
        try:
            if self._opencode_created and self._opencode_dir.exists():
                shutil.rmtree(self._opencode_dir, ignore_errors=True)
            else:
                for f in self._tools_created:
                    if f.exists():
                        f.unlink()
        except OSError:
            pass

    def _env(self) -> dict:
        env = dict(os.environ)
        env["AGENT_GRAPHS_CALLBACK_URL"] = self._callback_url
        env["AGENT_GRAPHS_CALLBACK_TOKEN"] = self._callback_token
        env["AGENT_GRAPHS_SESSION_ID"] = self.session_id
        # Our generated config is authoritative for this run (inline, so no file
        # in the repo); suppress auto-update chatter.
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(self._config)
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
        return env

    async def start(self, *, timeout: float = 30.0) -> None:
        """Boot the server (idempotent): stage config+tool, spawn ``opencode
        serve`` with cwd = the repo, wait until ``/config`` answers."""
        async with self._lock:
            if self.running:
                return
            self._stage(self.graph)
            if self._log_dir is None:
                self._log_dir = Path(tempfile.mkdtemp(prefix=f"ag_oc_{self.session_id}_"))
            self.port = _free_port()
            self.base_url = f"http://127.0.0.1:{self.port}"
            log = open(self._log_dir / "serve.log", "w")  # noqa: SIM115 — closed on shutdown
            self._log_file = log
            self._proc = await asyncio.create_subprocess_exec(
                self._binary, "serve", "--port", str(self.port), "--hostname", "127.0.0.1",
                cwd=str(self.repo_root),
                env=self._env(),
                stdout=log,
                stderr=log,
            )
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
            try:
                await self._await_ready(timeout)
            except Exception:
                # A failed boot (timeout / early exit) must not leak the spawned
                # process, the log handle, the client, or the staged tool — the
                # caller never gets a handle to shut us down.
                await self.shutdown(cleanup=True)
                raise

    async def _await_ready(self, timeout: float) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        last_err: Exception | None = None
        while asyncio.get_event_loop().time() < deadline:
            if not self.running:
                raise RuntimeError(f"opencode server exited early (see {self._log_dir}/serve.log)")
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
        """Apply a graph/spec change: re-stage config + restart the server
        (OpenCode reads config at boot). Sessions are recreated lazily after."""
        await self.shutdown(cleanup=False)
        self._stage(graph)
        await self.start()

    async def shutdown(self, *, cleanup: bool = True) -> None:
        """Terminate the process; remove the staged tool file + temp log dir."""
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
        if cleanup:
            self._unstage()
            if self._log_dir is not None:
                shutil.rmtree(self._log_dir, ignore_errors=True)
                self._log_dir = None
