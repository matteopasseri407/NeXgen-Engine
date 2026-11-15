"""mcp_bridge_channel.py — what the bridge needs on top of one stdio MCP server.

One job: be the bridge's answer for one connector — a JSON-RPC message in, a JSON-RPC
message out — over `nexgen_core.mcp_stdio`, which owns the process and the wire.

Three rules decide the shape of this file, and none of them is about transport.

The backend starts on the first request, not when the bridge starts, because a gateway
that mounts five connectors and uses one should not keep five interpreters resident.
`mcp_stdio` opens the child on the first call for exactly this reason.

A dead backend is a phrase, never an exception. Every failure path answers with a
JSON-RPC error the caller can print, and a backend found gone is replaced and the
request asked again: a bridge that has to be restarted by hand is worse than no bridge.

The id in the answer is the id the caller sent. Two HTTP callers sharing one endpoint
both send `id: 1` on their first call; the session's id space is the channel's, so the
reply is re-labelled before it leaves and one caller's answer can never reach the other.
"""
from __future__ import annotations

from typing import Any

#: How long one forwarded request may wait for its reply. Above the slowest tool this
#: engine knows (an OCR call, a browser navigation) so the bridge is never the shorter
#: of two timeouts; the HTTP client still owns its own limit.
DEFAULT_REQUEST_TIMEOUT = 300.0

#: The JSON-RPC code for an internal failure the protocol has no closer name for.
INTERNAL_ERROR = -32000


class StdioProcessChannel:
    """A child MCP server, reachable as one JSON-RPC request in and one reply out.

    The SDK-backed session and the SDK's error vocabulary are imported where they are
    used, not at the top of this file: the command line imports this module while it
    builds its parser, and a help screen must not load an async MCP stack.
    """

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        from nexgen_core.mcp_stdio import StdioSession

        self.command = list(command)
        self.env = dict(env or {})
        self.cwd = cwd
        self.request_timeout = request_timeout
        self._session = StdioSession(self.command, env=self.env, cwd=cwd)

    def send_request(self, message: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """Forwards one request and answers with the backend's reply, id restored."""
        from nexgen_core.mcp_stdio import CONNECTION_CLOSED, MCPError, leaf

        original_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        limit = self.request_timeout if timeout is None else timeout
        try:
            try:
                result = self._session.call(method, params, limit)
            except MCPError as exc:
                if exc.code != CONNECTION_CLOSED:
                    raise
                # The child this request met is already gone: the connection closed
                # under it, so there is no answer it could still be waiting for.
                self._session.restart()
                result = self._session.call(method, params, limit)
        except TimeoutError:
            return _error(original_id, INTERNAL_ERROR, "the backend did not answer in time")
        except MCPError as exc:
            return _error(original_id, exc.code, exc.message)
        except (OSError, RuntimeError, ValueError, ExceptionGroup) as exc:
            return _error(original_id, INTERNAL_ERROR, str(leaf(exc)))
        return {"jsonrpc": "2.0", "id": original_id, "result": result}

    def send_notification(self, message: dict[str, Any]) -> None:
        """Accepts a one-way message from the caller; it has nobody left to tell.

        The handshake with the child is the session's own, so `notifications/initialized`
        has already been sent, and a cancellation carries the caller's request id, which
        the child never saw: forwarding it would cancel a different request. The server
        still answers 202, because a notification is not something to answer.
        """

    def close(self) -> None:
        """Stops the child and refuses every later request."""
        self._session.close()


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """A JSON-RPC error reply for one request id."""
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
