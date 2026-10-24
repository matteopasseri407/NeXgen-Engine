"""mcp_bridge.py — Local Streamable HTTP bridge for stdio MCP servers.

Exposes local stdio MCP servers (like lazy-mcp or individual tools) as standard
MCP Streamable HTTP servers for harnesses running in environments where Windows
commands cannot be launched directly (e.g. OpenClaw Gateway in WSL2 without
interop or mount of C:).

Security:
- Listens on loopback (127.0.0.1) by default or the WSL host interface.
- Strictly requires a Bearer token on every request (never 0.0.0.0 without token).
- Tokens are stored in the engine's secrets store and never printed to stdout/logs.
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from nexgen_core.paths import resolve_engine_root, resolve_state_dir
from nexgen_core.processes import force_stop_process_tree, no_window_kwargs, spawn_detached

logger = logging.getLogger("nexgen.mcp.bridge")

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
DEFAULT_TOKEN_ENV = "NEXGEN_MCP_BRIDGE_TOKEN"
STREAMABLE_HTTP = "streamable-http"


def ensure_bridge_token(token_env_name: str = DEFAULT_TOKEN_ENV, home: Path | None = None) -> str:
    """Gets or generates the bridge bearer token, storing it securely without printing."""
    current = os.environ.get(token_env_name)
    if current and current.strip():
        return current.strip()

    from nexgen_core.deposit_env import deposit_path, read_deposit
    from nexgen_core.stack.secrets import ensure

    path = deposit_path()
    existing = read_deposit()
    if existing.get(token_env_name):
        token = existing[token_env_name]
        os.environ[token_env_name] = token
        return token

    # Generate and record securely
    ensure(path, [token_env_name])
    updated = read_deposit()
    token = updated.get(token_env_name) or secrets.token_hex(32)
    os.environ[token_env_name] = token
    return token


def detect_wsl_host_ip() -> str | None:
    """Detects the Windows host IPv4 address on the virtual WSL network interface."""
    explicit = os.environ.get("NEXGEN_WSL_HOST_IP")
    if explicit:
        return explicit.strip()

    if os.name == "nt":
        try:
            cmd = ["powershell.exe", "-NoProfile", "-Command",
                   "(Get-NetIPAddress -InterfaceAlias '*WSL*' -AddressFamily IPv4).IPAddress"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0, check=False)
            if res.returncode == 0 and res.stdout.strip():
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                if lines:
                    return lines[0]
        except (OSError, subprocess.TimeoutExpired):  # nexgen: allow reason="WSL interface detection is best-effort and falls back to loopback"
            pass
    return None


def resolve_listen_address(host: str | None = None, wsl: bool = False, token: str | None = None) -> tuple[str, str]:
    """Resolves (bind_address, client_address) ensuring protected exposure."""
    if wsl or (host and host.lower() == "wsl"):
        wsl_ip = detect_wsl_host_ip()
        if wsl_ip:
            return (wsl_ip, wsl_ip)
        return (DEFAULT_HOST, DEFAULT_HOST)

    if host:
        cleaned = host.strip()
        if cleaned in ("0.0.0.0", "::"):
            if not token or not token.strip():
                raise ValueError("Refusing to bind 0.0.0.0 without a bearer token")
            return (cleaned, DEFAULT_HOST)
        return (cleaned, cleaned)

    return (DEFAULT_HOST, DEFAULT_HOST)


class StdioProcessChannel:
    """Thread-safe JSON-RPC communication over a child process's stdio streams."""

    def __init__(self, command: list[str], cwd: str | None = None, env: dict[str, str] | None = None):
        self.command = command
        self.cwd = cwd
        self.env = env
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._pending: dict[Any, tuple[threading.Event, list[dict[str, Any]]]] = {}
        self._reader_thread: threading.Thread | None = None
        self._closed = False

    def _ensure_started(self) -> None:
        if self._closed:
            raise RuntimeError("Channel is closed")
        if self.proc is not None and self.proc.poll() is None:
            return

        kwargs = no_window_kwargs()
        self.proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
            text=True,
            encoding="utf-8",
            bufsize=1,
            **kwargs,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True, name="mcp-bridge-reader")
        self._reader_thread.start()

    def _reader_loop(self) -> None:
        while not self._closed and self.proc and self.proc.stdout:
            line = self.proc.stdout.readline()
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                msg = json.loads(stripped)
            except (ValueError, UnicodeError):  # nexgen: allow reason="ignore non-JSON output on child stdout before or between frames"
                continue
            if isinstance(msg, dict) and "id" in msg:
                req_id = msg["id"]
                with self._lock:
                    if req_id in self._pending:
                        event, slot = self._pending.pop(req_id)
                        slot.append(msg)
                        event.set()

        # Reached EOF or closed: release any remaining requests
        with self._lock:
            for req_id, (event, slot) in list(self._pending.items()):
                slot.append({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": "Backend process exited"}})
                event.set()
            self._pending.clear()

    def send_notification(self, msg: dict[str, Any]) -> None:
        """Sends a one-way notification without waiting for a reply."""
        self._ensure_started()
        with self._lock:
            if not self.proc or not self.proc.stdin:
                return
            line = json.dumps(msg, ensure_ascii=False) + "\n"
            self.proc.stdin.write(line)
            self.proc.stdin.flush()

    def send_request(self, msg: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """Sends a JSON-RPC request and waits for matching reply."""
        self._ensure_started()
        req_id = msg.get("id")
        event = threading.Event()
        slot: list[dict[str, Any]] = []
        with self._lock:
            self._pending[req_id] = (event, slot)
            line = json.dumps(msg, ensure_ascii=False) + "\n"
            if self.proc and self.proc.stdin:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            else:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": "Process unavailable"}}

        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": f"Request timed out after {timeout}s"}}
        return slot[0] if slot else {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": "No response"}}

    def close(self) -> None:
        self._closed = True
        proc = self.proc
        self.proc = None
        if proc:
            force_stop_process_tree(proc, timeout=2.0)


class McpHttpBridgeHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler implementing MCP Streamable HTTP with Bearer authentication."""

    server: McpHttpBridgeServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Silence default stderr logging to keep operational terminal clean
        pass

    def _send_json(self, status: int, data: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _authorize(self) -> bool:
        auth_header = self.headers.get("Authorization", "").strip()
        expected = f"Bearer {self.server.token}"
        if auth_header != expected:
            self._send_json(401, {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Unauthorized"}})
            return False
        return True

    def _resolve_channel(self) -> StdioProcessChannel | None:
        path = self.path.split("?")[0].rstrip("/")
        if path == "/mcp" or path == "":
            return self.server.default_channel
        if path.startswith("/mcp/"):
            name = path[5:].strip("/")
            if name in self.server.channels:
                return self.server.channels[name]
            return self.server.default_channel
        return None

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorize():
            return
        channel = self._resolve_channel()
        if channel is None:
            self._send_json(404, {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Endpoint not found: {self.path}"}})
            return
        # Per MCP Streamable HTTP specification, 405 indicates server does not offer standalone SSE stream on GET
        self._send_json(405, {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Streamable HTTP requires POST"}},
                        headers={"Allow": "POST, OPTIONS"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorize():
            return
        channel = self._resolve_channel()
        if channel is None:
            self._send_json(404, {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Endpoint not found: {self.path}"}})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        if content_length <= 0:
            self._send_json(400, {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Missing request body"}})
            return

        body = self.rfile.read(content_length)
        try:
            req = json.loads(body.decode("utf-8"))
        except Exception:
            self._send_json(400, {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}})
            return

        # Notification handling
        if isinstance(req, dict) and "id" not in req:
            channel.send_notification(req)
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # Request handling
        reply = channel.send_request(req)
        self._send_json(200, reply)


class McpHttpBridgeServer(http.server.ThreadingHTTPServer):
    """Server hosting the MCP HTTP Bridge."""

    def __init__(
        self,
        server_address: tuple[str, int],
        token: str,
        default_channel: StdioProcessChannel,
        channels: dict[str, StdioProcessChannel] | None = None,
        client_host: str | None = None,
    ):
        super().__init__(server_address, McpHttpBridgeHandler)
        self.token = token
        self.default_channel = default_channel
        self.channels = channels or {}
        self.client_host = client_host or server_address[0]
        self._thread: threading.Thread | None = None

    def start_in_thread(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True, name="mcp-bridge-server")
        self._thread.start()

    def get_url(self) -> str:
        port = self.server_address[1]
        host = self.client_host if self.client_host not in ("0.0.0.0", "::") else DEFAULT_HOST
        return f"http://{host}:{port}/mcp"

    def shutdown_and_close(self) -> None:
        self.shutdown()
        self.server_close()
        self.default_channel.close()
        for ch in self.channels.values():
            ch.close()


def state_file_path(home: Path | None = None) -> Path:
    """Path to the bridge daemon's state JSON file."""
    return resolve_state_dir(home) / "mcp_bridge.json"


def default_backend_command() -> list[str]:
    """Default stdio command to bridge: NeXgen's universal lazy-mcp aggregator."""
    engine_root = Path(resolve_engine_root())
    lazy_mcp = engine_root / "agent-universal-layer" / "mcp" / "lazy-mcp.py"
    return [sys.executable, str(lazy_mcp)]


def start_bridge(
    host: str | None = None,
    port: int = DEFAULT_PORT,
    wsl: bool = False,
    foreground: bool = False,
    home: Path | None = None,
    channel_command: list[str] | None = None,
    spawn_fn: Callable[[list[str]], int | None] | None = None,
) -> dict[str, Any]:
    """Starts the MCP HTTP bridge server."""
    state_file = state_file_path(home)
    if state_file.is_file():
        status = status_bridge(home)
        if status.get("running"):
            return {"status": "already_running", "url": status["url"], "pid": status.get("pid")}

    token = ensure_bridge_token(home=home)
    bind_h, client_h = resolve_listen_address(host, wsl=wsl, token=token)
    cmd = channel_command or default_backend_command()

    if foreground:
        channel = StdioProcessChannel(cmd)
        server = McpHttpBridgeServer((bind_h, port), token=token, default_channel=channel, client_host=client_h)
        info = {
            "pid": os.getpid(),
            "bind_host": bind_h,
            "port": server.server_address[1],
            "url": server.get_url(),
            "token_env": DEFAULT_TOKEN_ENV,
            "started_at": time.time(),
        }
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
        try:
            server.serve_forever()
        finally:
            server.shutdown_and_close()
            state_file.unlink(missing_ok=True)
        return info

    # Background detached process
    launcher_argv = [
        sys.executable, "-m", "nexgen_core.mcp_bridge", "run",
        "--host", bind_h,
        "--port", str(port),
    ]
    spawner = spawn_fn or spawn_detached
    pid = spawner(launcher_argv)
    info = {
        "pid": pid,
        "bind_host": bind_h,
        "port": port,
        "url": f"http://{client_h}:{port}/mcp",
        "token_env": DEFAULT_TOKEN_ENV,
        "started_at": time.time(),
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def stop_bridge(home: Path | None = None, force_stop_fn: Callable[[int], bool] | None = None) -> bool:
    """Stops the running bridge daemon, if active."""
    state_file = state_file_path(home)
    if not state_file.is_file():
        return False
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        pid = data.get("pid")
    except (OSError, ValueError):
        pid = None

    stopped = False
    if pid and isinstance(pid, int):
        if force_stop_fn:
            stopped = force_stop_fn(pid)
        else:
            class DummyProc:
                def __init__(self, p: int):
                    self.pid = p

                def poll(self):
                    return None

                def kill(self):
                    pass

                def wait(self, timeout=None):
                    return 0

            stopped = force_stop_process_tree(DummyProc(pid), timeout=3.0)

    state_file.unlink(missing_ok=True)
    return stopped or True


def status_bridge(home: Path | None = None, probe_fn: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """Inspects the running state of the MCP HTTP bridge."""
    state_file = state_file_path(home)
    if not state_file.is_file():
        return {"running": False, "message": "MCP HTTP bridge is not running"}

    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state_file.unlink(missing_ok=True)
        return {"running": False, "message": "Corrupt bridge state file removed"}

    pid = data.get("pid")
    url = data.get("url")
    return {
        "running": True,
        "pid": pid,
        "url": url,
        "bind_host": data.get("bind_host"),
        "port": data.get("port"),
        "token_env": data.get("token_env", DEFAULT_TOKEN_ENV),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI runner entry point for the bridge daemon."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m nexgen_core.mcp_bridge [run|start|stop|status] [options]")
        return 1

    verb = args[0]
    if verb == "run":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--host", default=DEFAULT_HOST)
        parser.add_argument("--port", type=int, default=DEFAULT_PORT)
        parsed = parser.parse_args(args[1:])
        start_bridge(host=parsed.host, port=parsed.port, foreground=True)
        return 0
    if verb == "stop":
        stop_bridge()
        return 0
    if verb == "status":
        info = status_bridge()
        print(json.dumps(info, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
