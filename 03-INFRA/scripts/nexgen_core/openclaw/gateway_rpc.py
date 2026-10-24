"""OpenClaw Gateway WebSocket RPC client for configuration and plugin management.

This module provides the low-level RPC transport connecting NeXgen Engine to
OpenClaw Gateway via WebSocket on protocol v4. It authenticates as an operator
client, enforces optimistic concurrency on config updates, handles plugin
installation and reloading, and wraps raw transport failures in clean, readable
exceptions without leaking credentials.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:  # annotations only: the library itself is imported when a socket opens
    import websockets.sync.client as ws_sync

from nexgen_core import __version__ as ENGINE_VERSION
from nexgen_core.openclaw import ws_transport
from nexgen_core.paths import openclaw_config

DEFAULT_GATEWAY_PORT = 18789
DEFAULT_GATEWAY_URL = f"ws://127.0.0.1:{DEFAULT_GATEWAY_PORT}"
DEFAULT_TIMEOUT = 10.0
PROTOCOL_VERSION = 4
CLIENT_ID = "cli"
CLIENT_MODE = "cli"
CLIENT_DISPLAY_NAME = "nexgen"
SCOPE_READ = "operator.read"
SCOPE_WRITE = "operator.write"
SCOPE_ADMIN = "operator.admin"


class GatewayRpcError(Exception):
    """Base exception for all OpenClaw Gateway RPC errors."""


class GatewayConnectionError(GatewayRpcError):
    """The gateway is unreachable, shut down, or closed the connection unexpectedly."""


class GatewayAuthError(GatewayRpcError):
    """Authentication failed: token rejected or credentials missing."""


class GatewayProtocolError(GatewayRpcError):
    """The gateway protocol version or handshake payload is incompatible."""


class GatewayScopeError(GatewayRpcError):
    """The authenticated client lacks the required scope for this operation."""


class GatewayConflictError(GatewayRpcError):
    """Optimistic concurrency check failed: base hash conflict on config.patch."""


class GatewayTimeoutError(GatewayConnectionError):
    """The gateway did not respond within the allocated timeout."""


class GatewayResponseError(GatewayRpcError):
    """The gateway returned a typed error response."""

    def __init__(self, message: str, code: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _socket_api() -> tuple[Any, tuple[type[BaseException], ...]]:
    """The socket client and its errors; a missing library is this module's own error."""
    try:
        return ws_transport.socket_api()
    except ws_transport.SocketLibraryMissing as exc:
        raise GatewayConnectionError(str(exc)) from exc


def resolve_gateway_url(
    url: str | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the WebSocket URL for OpenClaw Gateway.

    Precedence: explicit url arg, OPENCLAW_GATEWAY_URL, OPENCLAW_GATEWAY_PORT,
    openclaw.json (gateway.url or gateway.port), then default loopback port 18789.
    """
    if url and url.strip():
        return url.strip()
    environ = os.environ if env is None else env
    if environ.get("OPENCLAW_GATEWAY_URL"):
        return environ["OPENCLAW_GATEWAY_URL"].strip()
    if environ.get("OPENCLAW_GATEWAY_PORT"):
        return f"ws://127.0.0.1:{environ['OPENCLAW_GATEWAY_PORT'].strip()}"
    cfg_path = openclaw_config(home=home, env=env)
    if cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            gw = data.get("gateway") if isinstance(data, dict) else None
            if isinstance(gw, dict):
                if gw.get("url") and isinstance(gw["url"], str):
                    return gw["url"].strip()
                if gw.get("port") and isinstance(gw["port"], int):
                    return f"ws://127.0.0.1:{gw['port']}"
        except (OSError, json.JSONDecodeError, KeyError, TypeError):  # nexgen: allow reason="missing or invalid openclaw.json falls back to default gateway url"
            pass
    return DEFAULT_GATEWAY_URL


def resolve_gateway_token(
    token: str | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve the authentication token for OpenClaw Gateway.

    Precedence: explicit token arg, OPENCLAW_GATEWAY_TOKEN, or openclaw.json gateway.auth.token.
    The resolved token must never be logged or printed.
    """
    if token and token.strip():
        return token.strip()
    environ = os.environ if env is None else env
    if environ.get("OPENCLAW_GATEWAY_TOKEN"):
        return environ["OPENCLAW_GATEWAY_TOKEN"].strip()
    cfg_path = openclaw_config(home=home, env=env)
    if cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            gw = data.get("gateway") if isinstance(data, dict) else None
            auth = gw.get("auth") if isinstance(gw, dict) else None
            if isinstance(auth, dict) and isinstance(auth.get("token"), str):
                t_val = auth["token"].strip()
                if t_val:
                    return t_val
        except (OSError, json.JSONDecodeError, KeyError, TypeError):  # nexgen: allow reason="missing or invalid openclaw.json falls back to unauthenticated or env token"
            pass
    return None


def _normalize_plugin_source(source: dict[str, Any] | str) -> dict[str, Any]:
    """Convert a plugin install source specifier or dict to gateway RPC params."""
    if isinstance(source, dict):
        return dict(source)
    s = str(source).strip()
    for prefix in ("npm", "clawhub", "git", "npm-pack", "bundled", "official", "local"):
        if s.startswith(f"{prefix}:"):
            target = s[len(prefix) + 1:].strip()
            if prefix == "clawhub":
                return {"source": "clawhub", "packageName": target}
            if prefix in ("npm", "git"):
                return {"source": prefix, "spec": target}
            if prefix in ("bundled", "official"):
                return {"source": prefix, "pluginId": target}
            if prefix == "npm-pack":
                return {"source": "npm-pack", "archivePath": target}
            if prefix == "local":
                return {"source": "local", "path": target}
    if s.startswith((".", "/", "\\")) or os.sep in s or "/" in s:
        return {"source": "local", "path": s}
    return {"source": "npm", "spec": s}


class GatewayRpcClient:
    """WebSocket JSON-RPC client speaking the OpenClaw Gateway Protocol."""

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        home: Path | None = None,
        env: Mapping[str, str] | None = None,
        scopes: list[str] | tuple[str, ...] | None = None,
        client_id: str = CLIENT_ID,
        client_mode: str = CLIENT_MODE,
        client_display_name: str = CLIENT_DISPLAY_NAME,
        client_version: str = ENGINE_VERSION,
    ) -> None:
        self.url = resolve_gateway_url(url, home=home, env=env)
        self._token = resolve_gateway_token(token, home=home, env=env)
        self.default_timeout = float(timeout)
        self.scopes = list(scopes) if scopes is not None else [SCOPE_READ]
        self.client_id = client_id
        self.client_mode = client_mode
        self.client_display_name = client_display_name
        self.client_version = client_version
        self._ws: ws_sync.ClientConnection | None = None
        self._hello_payload: dict[str, Any] | None = None

    def __repr__(self) -> str:
        # Intentionally omit the authentication token to prevent credential leaks
        return f"GatewayRpcClient(url={self.url!r}, connected={self.is_connected})"

    @property
    def is_connected(self) -> bool:
        """True if the client currently holds an open WebSocket connection."""
        return self._ws is not None

    def connect(self) -> None:
        """Connect to the gateway, wait for optional challenge, and complete handshake."""
        if self._ws is not None:
            return
        client, transport_errors = _socket_api()
        try:
            ws = client.connect(
                self.url,
                open_timeout=self.default_timeout,
                close_timeout=2.0,
            )
        except TimeoutError:
            raise GatewayTimeoutError(f"Connection to gateway at {self.url} timed out") from None
        except (OSError, *transport_errors) as e:
            raise GatewayConnectionError(f"Gateway is not reachable at {self.url}: {e}") from None

        self._ws = ws
        try:
            self._perform_handshake()
        except Exception:
            self.close()
            raise

    def _perform_handshake(self) -> None:
        """Execute the OpenClaw connect challenge / hello-ok handshake."""
        assert self._ws is not None
        _, transport_errors = _socket_api()
        try:
            raw_challenge = self._ws.recv(timeout=0.2)
            parsed_ch = json.loads(raw_challenge)
            if isinstance(parsed_ch, dict) and parsed_ch.get("event") == "connect.challenge":
                pass
        except TimeoutError:  # nexgen: allow reason="pre-connect challenge is optional in older or fast gateways"
            pass
        except (*transport_errors, OSError, json.JSONDecodeError):  # nexgen: allow reason="non-challenge initial frame is handled by connect handshake"
            pass

        req_id = str(uuid.uuid4())
        connect_params: dict[str, Any] = {
            "minProtocol": PROTOCOL_VERSION,
            "maxProtocol": PROTOCOL_VERSION,
            "client": {
                "id": self.client_id,
                "version": self.client_version,
                "platform": sys.platform,
                "mode": self.client_mode,
                "displayName": self.client_display_name,
            },
            "role": "operator",
            "scopes": list(self.scopes),
        }
        if self._token:
            connect_params["auth"] = {"token": self._token}

        connect_req = {"type": "req", "id": req_id, "method": "connect", "params": connect_params}
        self._ws.send(json.dumps(connect_req))

        try:
            raw_res = self._ws.recv(timeout=self.default_timeout)
        except TimeoutError:
            raise GatewayTimeoutError("Gateway handshake timed out") from None
        except (*transport_errors, OSError) as e:
            raise GatewayConnectionError(f"Gateway disconnected during handshake: {e}") from None

        try:
            res = json.loads(raw_res)
        except json.JSONDecodeError as e:
            raise GatewayProtocolError(f"Gateway sent invalid JSON in handshake: {e}") from None

        self._validate_handshake_response(res)

    def _validate_handshake_response(self, res: dict[str, Any]) -> None:
        """Validate the hello-ok response or raise typed error."""
        if res.get("ok") is not True:
            err = res.get("error") if isinstance(res.get("error"), dict) else {}
            code = str(err.get("code") or "")
            msg = str(err.get("message") or "Gateway connect rejected")
            details = err.get("details") if isinstance(err.get("details"), dict) else {}
            detail_code = str(details.get("code") or "")

            if "AUTH" in code or "AUTH" in detail_code or "unauthorized" in msg.lower() or "token mismatch" in msg.lower():
                raise GatewayAuthError("Gateway authentication failed: token rejected")
            if "PROTOCOL" in code or "PROTOCOL" in detail_code or "protocol mismatch" in msg.lower():
                raise GatewayProtocolError(f"Gateway protocol mismatch: {msg}")
            if "FORBIDDEN" in code or "scope" in msg.lower() or "FORBIDDEN" in detail_code:
                raise GatewayScopeError(f"Gateway authorization error: {msg}")
            raise GatewayRpcError(f"Gateway connect rejected: {msg}")

        payload = res.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "hello-ok":
            type_name = payload.get("type") if isinstance(payload, dict) else type(payload).__name__
            raise GatewayProtocolError(f"Unexpected gateway connect response: expected hello-ok, got {type_name}")
        self._hello_payload = payload

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Dispatch a JSON-RPC request to the gateway and return its payload."""
        if not self.is_connected:
            self.connect()
        assert self._ws is not None
        _, transport_errors = _socket_api()

        req_id = str(uuid.uuid4())
        frame = {
            "type": "req",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        t_limit = float(timeout) if timeout is not None else self.default_timeout

        try:
            self._ws.send(json.dumps(frame))
        except (*transport_errors, OSError) as e:
            self.close()
            raise GatewayConnectionError(f"Failed to send RPC request {method!r}: {e}") from None

        while True:
            try:
                raw = self._ws.recv(timeout=t_limit)
            except TimeoutError:
                raise GatewayTimeoutError(f"Gateway request {method!r} timed out after {t_limit}s") from None
            except (*transport_errors, OSError) as e:
                self.close()
                raise GatewayConnectionError(f"Gateway connection lost during {method!r}: {e}") from None

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as e:
                raise GatewayProtocolError(f"Gateway sent invalid JSON: {e}") from None

            if isinstance(msg, dict) and msg.get("type") == "res" and msg.get("id") == req_id:
                return self._unpack_response(method, msg)

    def _unpack_response(self, method: str, msg: dict[str, Any]) -> dict[str, Any]:
        """Unpack a matching RPC response frame, raising typed errors on failure."""
        if msg.get("ok") is True:
            payload = msg.get("payload")
            return payload if isinstance(payload, dict) else {"result": payload}

        err = msg.get("error") if isinstance(msg.get("error"), dict) else {}
        code = str(err.get("code") or "")
        err_msg = str(err.get("message") or f"RPC {method} failed")
        details = err.get("details") if isinstance(err.get("details"), dict) else {}
        detail_code = str(details.get("code") or "")

        if method == "config.patch" and (
            "base hash" in err_msg.lower()
            or "conflict" in err_msg.lower()
            or "CONFIG_BASE_HASH_MISMATCH" in (code, detail_code)
            or details.get("type") == "conflict"
        ):
            raise GatewayConflictError(f"Config conflict: {err_msg}")

        if (
            "scope" in err_msg.lower()
            or "forbidden" in err_msg.lower()
            or code == "FORBIDDEN"
            or detail_code == "FORBIDDEN"
        ):
            raise GatewayScopeError(f"Gateway scope missing: {err_msg}")

        raise GatewayResponseError(f"Gateway RPC {method} failed ({code}): {err_msg}", code=code, details=details)

    def health(self, timeout: float | None = None) -> dict[str, Any]:
        """Fetch the health snapshot from OpenClaw Gateway."""
        return self.request("health", {}, timeout=timeout)

    def config_get(self, timeout: float | None = None) -> dict[str, Any]:
        """Fetch the current on-disk configuration snapshot and optimistic base hash."""
        return self.request("config.get", {}, timeout=timeout)

    def config_patch(
        self,
        patch: dict[str, Any] | str,
        base_hash: str | None = None,
        replace_paths: list[str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Apply an optimistic partial configuration update to OpenClaw Gateway."""
        raw = json.dumps(patch) if isinstance(patch, dict) else str(patch)
        params: dict[str, Any] = {"raw": raw}
        if base_hash is not None:
            params["baseHash"] = str(base_hash)
        if replace_paths is not None:
            params["replacePaths"] = list(replace_paths)
        return self.request("config.patch", params, timeout=timeout)

    def plugins_install(
        self,
        source: dict[str, Any] | str,
        timeout: float | None = 30.0,
    ) -> dict[str, Any]:
        """Install a plugin on OpenClaw Gateway from a source specifier or dict."""
        return self.request("plugins.install", _normalize_plugin_source(source), timeout=timeout or 30.0)

    def plugins_reload(
        self,
        plugin_id: str,
        timeout: float | None = 15.0,
    ) -> dict[str, Any]:
        """Reload an active plugin on OpenClaw Gateway by its identifier."""
        params = {"plugins": [{"pluginId": str(plugin_id).strip()}]}
        return self.request("plugins.reload", params, timeout=timeout or 15.0)

    def close(self) -> None:
        """Close the active WebSocket connection."""
        ws = self._ws
        self._ws = None
        if ws is not None:
            _, transport_errors = _socket_api()
            try:
                ws.close()
            except (*transport_errors, OSError):  # nexgen: allow reason="best-effort socket closure on cleanup"
                pass

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()


def health(
    url: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    scopes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Inspect OpenClaw Gateway health snapshot via WebSocket RPC."""
    req_scopes = list(scopes) if scopes is not None else [SCOPE_READ]
    with GatewayRpcClient(url=url, token=token, timeout=timeout, scopes=req_scopes) as client:
        return client.health(timeout=timeout)


def config_get(
    url: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    scopes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Read OpenClaw Gateway active configuration snapshot via WebSocket RPC."""
    req_scopes = list(scopes) if scopes is not None else [SCOPE_READ]
    with GatewayRpcClient(url=url, token=token, timeout=timeout, scopes=req_scopes) as client:
        return client.config_get(timeout=timeout)


def config_patch(
    patch: dict[str, Any] | str,
    base_hash: str | None = None,
    url: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    replace_paths: list[str] | None = None,
    scopes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Apply an optimistic partial configuration update to OpenClaw Gateway."""
    req_scopes = list(scopes) if scopes is not None else [SCOPE_ADMIN]
    with GatewayRpcClient(url=url, token=token, timeout=timeout, scopes=req_scopes) as client:
        return client.config_patch(
            patch=patch,
            base_hash=base_hash,
            replace_paths=replace_paths,
            timeout=timeout,
        )


def plugins_install(
    source: dict[str, Any] | str,
    url: str | None = None,
    token: str | None = None,
    timeout: float = 30.0,
    scopes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Install a plugin on OpenClaw Gateway via WebSocket RPC."""
    req_scopes = list(scopes) if scopes is not None else [SCOPE_ADMIN]
    with GatewayRpcClient(url=url, token=token, timeout=timeout, scopes=req_scopes) as client:
        return client.plugins_install(source=source, timeout=timeout)


def plugins_reload(
    plugin_id: str,
    url: str | None = None,
    token: str | None = None,
    timeout: float = 15.0,
    scopes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Reload an active plugin on OpenClaw Gateway via WebSocket RPC."""
    req_scopes = list(scopes) if scopes is not None else [SCOPE_ADMIN]
    with GatewayRpcClient(url=url, token=token, timeout=timeout, scopes=req_scopes) as client:
        return client.plugins_reload(plugin_id=plugin_id, timeout=timeout)
