"""mcp_bridge_server.py — the HTTP side of the bridge: Streamable HTTP, bearer auth, one path per server.

One job: speak the MCP Streamable HTTP transport to a caller and hand each request
to the stdio channel that owns that connector. It knows nothing about where its
token is stored or how it was started; `nexgen_core.mcp_bridge` owns those.

Shape choices, all of them dictated by the standard or by what the gateway sends:

- answers are `application/json`, one JSON-RPC message per POST, never a stream.
  Streamable HTTP allows that (`GET` on the endpoint is what a server without a
  standalone stream answers with 405, which is what this one does);
- a notification is accepted with 202 and forwarded, because `initialize` and
  `notifications/initialized` are how a session opens;
- `Mcp-Session-Id` is not issued: the bridge is stateless towards the client and
  the backend keeps whatever session it keeps, so a gateway restart does not leave
  a session the bridge has forgotten about;
- every path and the health probe require the bearer token, before anything is
  parsed or forwarded.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from nexgen_core.mcp_bridge_channel import StdioProcessChannel

logger = logging.getLogger("nexgen.mcp.bridge")

#: The endpoint a gateway should be pointed at. One connector at `/mcp/<name>`.
BRIDGE_PATH = "/mcp"

#: What `nexgen mcp bridge status` asks: is this listener the one the state file names?
HEALTH_PATH = "/health"

#: How the owner stops the bridge without touching a pid.
SHUTDOWN_PATH = "/shutdown"

#: The server that the bare `/mcp` path is an alias for: the gateway this engine
#: already mounts everywhere else, so pointing a new harness at the bridge needs
#: no name to remember.
DEFAULT_ALIAS = "lazy-mcp"

#: The largest request body worth reading. A tool call with a file in it is still
#: far below this; anything above is a mistake or an attempt.
MAX_BODY_BYTES = 4 * 1024 * 1024


class McpHttpBridgeHandler(BaseHTTPRequestHandler):
    """One HTTP request: authenticate, pick the connector, forward, answer."""

    server: "McpHttpBridgeServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Keeps the transport's own access log off a terminal the person reads."""
        logger.debug("mcp bridge: " + format, *args)

    # -- replies ---------------------------------------------------------

    def _send_json(self, status: int, data: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, code: int, message: str, headers: dict[str, str] | None = None) -> None:
        self._send_json(status, {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}, headers)

    def _authorize(self) -> bool:
        """Refuses anything that does not carry this run's bearer token."""
        presented = self.headers.get("Authorization", "").strip()
        expected = f"Bearer {self.server.token}"
        if not hmac.compare_digest(presented, expected):
            self._error(401, -32001, "Unauthorized", {"WWW-Authenticate": "Bearer"})
            return False
        return True

    def _connector(self) -> StdioProcessChannel | None:
        """The channel this path addresses, or None when the path names no connector."""
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == BRIDGE_PATH:
            return self.server.default_channel
        prefix = BRIDGE_PATH + "/"
        if path.startswith(prefix):
            return self.server.channels.get(path[len(prefix):])
        return None

    # -- verbs -----------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 - the name http.server dispatches on
        self.send_response(204)
        self.send_header("Allow", "POST, GET, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - the name http.server dispatches on
        if not self._authorize():
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == HEALTH_PATH:
            self._send_json(200, self.server.health())
            return
        if self._connector() is not None:
            # Streamable HTTP: a server that offers no standalone stream says 405
            # rather than holding the connection open for nothing.
            self._error(405, -32601, "This bridge answers POST requests only", {"Allow": "POST, OPTIONS"})
            return
        self._error(404, -32601, f"No connector at {self.path}")

    def do_POST(self) -> None:  # noqa: N802 - the name http.server dispatches on
        if not self._authorize():
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == SHUTDOWN_PATH:
            self._shutdown()
            return
        channel = self._connector()
        if channel is None:
            self._discard_body()
            self._error(404, -32601, f"No connector at {self.path}")
            return
        message = self._read_message()
        if message is None:
            return
        if "id" not in message:
            channel.send_notification(message)
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(200, channel.send_request(message))

    def _shutdown(self) -> None:
        """Answers, then stops the listener from another thread (serve_forever must return)."""
        self._send_json(200, {"status": "stopping"})
        threading.Thread(target=self.server.shutdown, daemon=True, name="mcp-bridge-stop").start()

    def _discard_body(self) -> None:
        """Reads a refused request's body, so Windows does not abort a client still sending it."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if 0 < length <= MAX_BODY_BYTES:
            self.rfile.read(length)
        elif length > MAX_BODY_BYTES:
            self.close_connection = True

    def _read_message(self) -> dict[str, Any] | None:
        """The request body as one JSON-RPC message, or None after answering with the reason."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._error(400, -32700, "Missing request body")
            return None
        if length > MAX_BODY_BYTES:
            # The body was never read, so this connection cannot carry another
            # request: say so instead of leaving the client waiting on a desync.
            self.close_connection = True
            self._error(413, -32600, "Request body too large", {"Connection": "close"})
            return None
        try:
            message = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(400, -32700, "Parse error")
            return None
        if not isinstance(message, dict):
            self._error(400, -32600, "Only single JSON-RPC messages are accepted")
            return None
        if not message.get("method"):
            self._error(400, -32600, "Missing method")
            return None
        return message


class McpHttpBridgeServer(ThreadingHTTPServer):
    """The listener: one channel per connector and the paths that address them."""

    #: A request that never finishes must not keep the process alive on stop.
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        token: str,
        backends: dict[str, dict[str, Any]],
        client_host: str | None = None,
        nonce: str | None = None,
    ) -> None:
        self.token = token
        self.nonce = nonce or ""
        self.backends = backends
        self.channels: dict[str, StdioProcessChannel] = {
            str(name): StdioProcessChannel(list(spec["command"]), env=spec.get("env"))
            for name, spec in backends.items()
        }
        self.client_host = client_host or server_address[0]
        self.started_at = time.time()
        self._thread: threading.Thread | None = None
        self._served = False
        super().__init__(server_address, McpHttpBridgeHandler)

    @property
    def default_channel(self) -> StdioProcessChannel | None:
        """What bare `/mcp` serves: the gateway, when the bridge fronts it."""
        return self.channels.get(DEFAULT_ALIAS)

    def served_names(self) -> list[str]:
        """The connectors reachable at `/mcp/<name>`, in a stable order."""
        return sorted(self.channels)

    def get_url(self, name: str | None = None) -> str:
        """The URL a gateway should be given, for the alias or for one connector."""
        host = self.client_host if self.client_host not in ("0.0.0.0", "::") else "127.0.0.1"
        port = self.server_address[1]
        return f"http://{host}:{port}{BRIDGE_PATH}" + (f"/{name}" if name else "")

    def health(self) -> dict[str, Any]:
        """What the status probe reads: proof this process is the one the state file names."""
        return {
            "status": "ok",
            "pid": os.getpid(),
            "nonce": self.nonce,
            "url": self.get_url(),
            "servers": self.served_names(),
            "started_at": self.started_at,
        }

    def start_in_thread(self) -> None:
        """Serves in a background thread, for tests and for an in-process start."""
        self._thread = threading.Thread(target=self.serve, daemon=True, name="mcp-bridge-server")
        self._thread.start()

    def serve(self) -> None:
        """Blocks, answering requests until `shutdown` is called."""
        self._served = True
        self.serve_forever()

    def shutdown_and_close(self) -> None:
        """Stops accepting, then stops every backend it started."""
        if self._served:
            # `shutdown` waits for serve_forever to return, so it is never called
            # on a server that never ran: that would wait forever.
            self.shutdown()
        self.server_close()
        for channel in self.channels.values():
            channel.close()
