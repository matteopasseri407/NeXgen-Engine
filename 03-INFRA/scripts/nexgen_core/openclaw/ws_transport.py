"""The WebSocket client and its error types, imported when a connection is opened.

One job: keep the socket library out of the package's import path.
`nexgen_core.runtimes.openclaw` imports this package, the package imports the
RPC client, and a top-level `import websockets` there made an optional
dependency a requirement of every command -- the V2 smoke job installs pytest
and pyyaml only and died during collection. The import happens here, at the
first socket, and its absence is one sentence naming the package.
"""
from __future__ import annotations

from typing import Any


class SocketLibraryMissing(Exception):
    """The socket library is not installed; the caller turns this into its own error."""


#: The client module and the two transport errors, filled on first use.
_API: tuple[Any, tuple[type[BaseException], ...]] | None = None


def socket_api() -> tuple[Any, tuple[type[BaseException], ...]]:
    """The client module and the errors it raises, imported once, on the first socket."""
    global _API
    if _API is None:
        try:
            import websockets.exceptions
            import websockets.sync.client
        except ModuleNotFoundError as exc:
            raise SocketLibraryMissing(
                "the `websockets` package is not installed: it is needed to reach the OpenClaw gateway"
            ) from exc
        _API = (
            websockets.sync.client,
            (websockets.exceptions.ConnectionClosed, websockets.exceptions.WebSocketException),
        )
    return _API
