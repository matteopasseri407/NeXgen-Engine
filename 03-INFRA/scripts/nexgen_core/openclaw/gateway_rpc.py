"""Ask the OpenClaw gateway for something, through the CLI that is already its client.

One job: turn an engine call into `openclaw gateway call <method> --params <json>
--json --timeout <ms>`, and turn the answer back into the engine's own typing.

The CLI speaks the gateway protocol already: it resolves the URL (its config,
`OPENCLAW_GATEWAY_URL`, the loopback port), authenticates with the token that
gateway configured, asks for the least-privilege operator scopes the method
needs, and prints the payload as JSON. The engine used to carry a WebSocket
client for the same job -- 506 lines here plus a 39-line module whose only
purpose was keeping the socket library out of every command's import path, and
the `websockets` dependency with it. What stays here is the engine's knowledge
of the payloads and the error classes its callers branch on.
"""
from __future__ import annotations

import json
from typing import Any, Final

from nexgen_core.openclaw.node_cli import run_command
from nexgen_core.openclaw_service import RunResult

#: The program the CLI is reached by; `node_cli` knows how to start it here.
COMMAND = "openclaw"

#: What a call waits for the gateway. The CLI has the same default.
DEFAULT_TIMEOUT = 10.0

#: Extra time on top of the RPC timeout before the process is killed: every call
#: is a fresh node start, and `node_cli` measured a cold Windows start at fifteen
#: to twenty-five seconds before the gateway is asked anything at all.
STARTUP_GRACE_S = 30.0

#: A CLI answer that is not JSON; distinct from the JSON value `null`.
_UNPARSED: Final = object()


class GatewayRpcError(Exception):
    """Base exception for all OpenClaw Gateway RPC errors."""


class GatewayConnectionError(GatewayRpcError):
    """The gateway is unreachable, shut down, or the CLI could not reach it."""


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


def _json_value(text: str) -> Any:
    """The JSON value the CLI printed, or `_UNPARSED` when it printed something else."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _UNPARSED


def _sentence(result: RunResult) -> str:
    """What to tell the person when the CLI failed without a machine-readable answer."""
    lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
    if lines:
        return lines[-1]
    said = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if said:
        return said[0]
    return f"the {COMMAND} CLI exited {result.returncode} without an answer"


def _classify(method: str, error: dict[str, Any]) -> GatewayRpcError:
    """The engine's error for one of the CLI's failure envelopes.

    The CLI publishes a small set of shapes (`gateway_transport_error`,
    `gateway_request_error`, `gateway_credentials_required`, `cli_error`) and the
    engine's callers branch on what went wrong rather than on those names, so the
    mapping lives here and nowhere else. Nothing in it ever carries the token:
    the sentences come from the answer, never from the argv that asked for it.
    """
    kind = str(error.get("type") or "")
    message = str(error.get("message") or f"Gateway RPC {method} failed").strip()
    code = str(error.get("code") or "")
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    detail_code = str(details.get("code") or "")
    lowered = message.lower()

    if (kind == "gateway_credentials_required"
            or "AUTH" in code or "AUTH" in detail_code
            or "unauthorized" in lowered or "token mismatch" in lowered or "authentication failed" in lowered):
        return GatewayAuthError(f"Gateway authentication failed: {message}")
    if "PROTOCOL" in code or "PROTOCOL" in detail_code or "protocol mismatch" in lowered:
        return GatewayProtocolError(f"Gateway protocol mismatch: {message}")
    if kind == "gateway_transport_error":
        if code == "ETIMEDOUT" or "timed out" in lowered or "timeout" in lowered:
            return GatewayTimeoutError(message)
        return GatewayConnectionError(message)
    if method == "config.patch" and (
        "base hash" in lowered
        or "conflict" in lowered
        or "CONFIG_BASE_HASH_MISMATCH" in (code, detail_code)
        or details.get("type") == "conflict"
    ):
        return GatewayConflictError(f"Config conflict: {message}")
    if "scope" in lowered or "forbidden" in lowered or code == "FORBIDDEN" or detail_code == "FORBIDDEN":
        return GatewayScopeError(f"Gateway scope missing: {message}")
    return GatewayResponseError(f"Gateway RPC {method} failed ({code}): {message}", code=code, details=details)


def _answer(result: RunResult, method: str) -> dict[str, Any]:
    """The payload the CLI printed, or the engine's error for why it printed none."""
    parsed = _json_value((result.stdout or "").strip())
    if result.returncode == 0:
        if parsed is _UNPARSED:
            raise GatewayProtocolError(
                f"the {COMMAND} CLI answered {method!r} without a JSON payload: {_sentence(result)}")
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict):
        raise _classify(method, error)
    if parsed is _UNPARSED:
        raise GatewayConnectionError(_sentence(result))
    raise GatewayConnectionError(
        f"the {COMMAND} CLI exited {result.returncode} without an error it could describe")


def call_gateway(
    method: str,
    params: dict[str, Any] | None = None,
    url: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Ask the gateway one method and return its payload.

    `url` and `token` are passed to the CLI only when the caller supplied them:
    an omitted token is resolved by the CLI from its own config and environment,
    which is the same token the gateway was configured with, and it never
    travels through the engine's argv or memory.
    """
    argv = [
        COMMAND, "gateway", "call", str(method),
        "--params", json.dumps(params or {}, ensure_ascii=False),
        "--json",
        "--timeout", str(int(timeout * 1000)),
    ]
    if url and url.strip():
        argv.extend(["--url", url.strip()])
    if token and token.strip():
        argv.extend(["--token", token.strip()])
    return _answer(run_command(argv, timeout + STARTUP_GRACE_S), str(method))


def health(
    url: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Inspect the OpenClaw Gateway health snapshot."""
    return call_gateway("health", {}, url=url, token=token, timeout=timeout)


def config_get(
    url: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Read the active configuration snapshot and the base hash a patch needs."""
    return call_gateway("config.get", {}, url=url, token=token, timeout=timeout)


def config_patch(
    patch: dict[str, Any] | str,
    base_hash: str | None = None,
    url: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    replace_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Apply an optimistic partial configuration update to the gateway."""
    params: dict[str, Any] = {"raw": json.dumps(patch) if isinstance(patch, dict) else str(patch)}
    if base_hash is not None:
        params["baseHash"] = str(base_hash)
    if replace_paths is not None:
        params["replacePaths"] = list(replace_paths)
    return call_gateway("config.patch", params, url=url, token=token, timeout=timeout)


def _plugin_source(source: dict[str, Any] | str) -> dict[str, Any]:
    """The gateway's install params for one source specifier or dict."""
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
    if s.startswith((".", "/", "\\")) or "/" in s or "\\" in s:
        return {"source": "local", "path": s}
    return {"source": "npm", "spec": s}


def plugins_install(
    source: dict[str, Any] | str,
    url: str | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Install a plugin on the gateway from a source specifier or dict."""
    return call_gateway("plugins.install", _plugin_source(source), url=url, token=token, timeout=timeout)


def plugins_reload(
    plugin_id: str,
    url: str | None = None,
    token: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Reload an active plugin on the gateway by its identifier."""
    params = {"plugins": [{"pluginId": str(plugin_id).strip()}]}
    return call_gateway("plugins.reload", params, url=url, token=token, timeout=timeout)
