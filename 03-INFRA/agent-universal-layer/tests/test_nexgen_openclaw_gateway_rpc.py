"""Tests for OpenClaw Gateway WebSocket RPC client with a fake gateway server.

These tests use only ephemeral loopback WebSocket servers and temporary directories.
No real gateway, no real network connections, and no credential files are touched.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import websockets.sync.server as ws_server
from websockets.exceptions import WebSocketException

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli.openclaw_cmds import cmd_rpc_health  # noqa: E402
from nexgen_core.openclaw.gateway_rpc import (  # noqa: E402
    GatewayAuthError,
    GatewayConflictError,
    GatewayConnectionError,
    GatewayProtocolError,
    GatewayRpcClient,
    GatewayScopeError,
    GatewayTimeoutError,
    config_get,
    config_patch,
    health,
    plugins_install,
    plugins_reload,
    resolve_gateway_token,
    resolve_gateway_url,
)


def _handle_handshake(
    ws,
    req_id: str,
    connect_req: dict,
    expected_token: str,
    current_hash: str,
    reject_auth: bool,
    protocol_error: bool,
) -> bool:
    """Respond to connect request frame during mock handshake."""
    if protocol_error:
        ws.send(json.dumps({
            "type": "res",
            "id": req_id,
            "ok": False,
            "error": {
                "code": "PROTOCOL_MISMATCH",
                "message": "protocol mismatch: expected 4, got 3",
                "details": {"code": "PROTOCOL_MISMATCH"},
            },
        }))
        return False
    auth_val = connect_req.get("params", {}).get("auth", {}).get("token")
    if reject_auth or auth_val != expected_token:
        ws.send(json.dumps({
            "type": "res",
            "id": req_id,
            "ok": False,
            "error": {
                "code": "AUTH_TOKEN_MISMATCH",
                "message": "authentication failed: invalid gateway token",
                "details": {"code": "AUTH_TOKEN_MISMATCH"},
            },
        }))
        return False
    ws.send(json.dumps({
        "type": "res",
        "id": req_id,
        "ok": True,
        "payload": {
            "type": "hello-ok",
            "protocol": 4,
            "server": {"version": "2026.10.6", "connId": "mock-conn-1"},
            "features": {
                "methods": ["health", "config.get", "config.patch", "plugins.install", "plugins.reload"],
                "events": [],
            },
            "snapshot": {"appliedConfigHash": current_hash},
            "auth": {
                "role": "operator",
                "scopes": ["operator.admin", "operator.read", "operator.write"],
            },
            "policy": {"maxPayload": 25 * 1024 * 1024, "tickIntervalMs": 15000},
        },
    }))
    return True


def _dispatch_config_rpc(ws, msg_id: str, method: str, params: dict, current_hash: str) -> bool:
    """Handle config.get and config.patch RPC methods."""
    if method == "config.get":
        ws.send(json.dumps({
            "type": "res",
            "id": msg_id,
            "ok": True,
            "payload": {
                "hash": current_hash,
                "raw": "{\"gateway\": {\"port\": 18789}}",
                "config": {"gateway": {"port": 18789}},
                "valid": True,
                "exists": True,
            },
        }))
        return True
    if method == "config.patch":
        if params.get("baseHash") != current_hash:
            ws.send(json.dumps({
                "type": "res",
                "id": msg_id,
                "ok": False,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "config base hash mismatch; re-run config.get and retry",
                    "details": {"code": "CONFIG_BASE_HASH_MISMATCH"},
                },
            }))
        else:
            ws.send(json.dumps({
                "type": "res",
                "id": msg_id,
                "ok": True,
                "payload": {"ok": True, "changedPaths": ["agents"], "path": "/mock/openclaw.json"},
            }))
        return True
    return False


def _dispatch_mock_rpc(
    ws,
    msg_id: str,
    method: str,
    params: dict,
    current_hash: str,
    scope_error: bool,
) -> None:
    """Dispatch simulated RPC methods and send structured mock responses."""
    if scope_error:
        ws.send(json.dumps({
            "type": "res",
            "id": msg_id,
            "ok": False,
            "error": {"code": "FORBIDDEN", "message": "missing scope", "details": {"code": "FORBIDDEN"}},
        }))
        return
    if method == "health":
        ws.send(json.dumps({
            "type": "res",
            "id": msg_id,
            "ok": True,
            "payload": {"ok": True, "uptimeMs": 42000, "plugins": {"loaded": ["nexgen"], "errors": []}},
        }))
    elif _dispatch_config_rpc(ws, msg_id, method, params, current_hash):
        pass
    elif method == "plugins.install":
        ws.send(json.dumps({
            "type": "res",
            "id": msg_id,
            "ok": True,
            "payload": {"ok": True, "installed": True, "source": params.get("source")},
        }))
    elif method == "plugins.reload":
        plugins = params.get("plugins", [])
        p_ids = [p.get("pluginId") for p in plugins]
        ws.send(json.dumps({
            "type": "res",
            "id": msg_id,
            "ok": True,
            "payload": {"ok": True, "pluginIds": p_ids, "restartRequired": False},
        }))


def _serve_client(
    ws,
    expected_token: str,
    current_hash: str,
    reject_auth: bool,
    protocol_error: bool,
    scope_error: bool,
    hang: bool,
    send_challenge: bool,
    recorded_requests: list[dict] | None = None,
) -> None:
    """Serve a single client connection with handshake and RPC loop."""
    if hang:
        import time
        time.sleep(5.0)
        return
    if send_challenge:
        ws.send(json.dumps({
            "type": "event",
            "event": "connect.challenge",
            "payload": {"nonce": "test-nonce", "ts": 1737 * 1000 * 1000},
        }))
    try:
        connect_raw = ws.recv(timeout=2.0)
        connect_req = json.loads(connect_raw)
        if recorded_requests is not None:
            recorded_requests.append(connect_req)
    except (TimeoutError, OSError, json.JSONDecodeError, WebSocketException):
        return
    req_id = str(connect_req.get("id"))
    if not _handle_handshake(ws, req_id, connect_req, expected_token, current_hash, reject_auth, protocol_error):
        return
    while True:
        try:
            raw = ws.recv(timeout=2.0)
            req = json.loads(raw)
        except (TimeoutError, OSError, json.JSONDecodeError, WebSocketException):
            break
        _dispatch_mock_rpc(
            ws,
            str(req.get("id")),
            str(req.get("method")),
            req.get("params") or {},
            current_hash,
            scope_error,
        )


@contextmanager
def fake_gateway_server(
    expected_token: str = "secret-token",
    current_hash: str = "hash-v1",
    reject_auth: bool = False,
    protocol_error: bool = False,
    scope_error: bool = False,
    hang: bool = False,
    send_challenge: bool = True,
    recorded_requests: list[dict] | None = None,
) -> Iterator[str]:
    """Ephemeral WebSocket server implementing the OpenClaw Gateway Protocol mock."""

    def handler(ws):
        _serve_client(
            ws,
            expected_token=expected_token,
            current_hash=current_hash,
            reject_auth=reject_auth,
            protocol_error=protocol_error,
            scope_error=scope_error,
            hang=hang,
            send_challenge=send_challenge,
            recorded_requests=recorded_requests,
        )

    with ws_server.serve(handler, "127.0.0.1", 0) as s:
        port = s.socket.getsockname()[1]
        t = threading.Thread(target=s.serve_forever, daemon=True)
        t.start()
        try:
            yield f"ws://127.0.0.1:{port}"
        finally:
            s.shutdown()
            t.join(timeout=1.0)


def test_rpc_operations_request_minimum_scopes():
    """Each RPC helper asks only for the minimum scope required by the OpenClaw protocol."""
    # health requests only operator.read
    reqs: list[dict] = []
    with fake_gateway_server(expected_token="tok", recorded_requests=reqs) as url:
        health(url=url, token="tok", timeout=2.0)
    assert reqs[0]["params"]["scopes"] == ["operator.read"]

    # config_get requests only operator.read
    reqs = []
    with fake_gateway_server(expected_token="tok", recorded_requests=reqs) as url:
        config_get(url=url, token="tok", timeout=2.0)
    assert reqs[0]["params"]["scopes"] == ["operator.read"]

    # config_patch requests operator.admin (reserved config.* prefix in protocol)
    reqs = []
    with fake_gateway_server(expected_token="tok", current_hash="hash-v1", recorded_requests=reqs) as url:
        config_patch({"foo": "bar"}, base_hash="hash-v1", url=url, token="tok", timeout=2.0)
    assert reqs[0]["params"]["scopes"] == ["operator.admin"]

    # plugins_install requests operator.admin
    reqs = []
    with fake_gateway_server(expected_token="tok", recorded_requests=reqs) as url:
        plugins_install("npm:foo", url=url, token="tok", timeout=2.0)
    assert reqs[0]["params"]["scopes"] == ["operator.admin"]

    # plugins_reload requests operator.admin
    reqs = []
    with fake_gateway_server(expected_token="tok", recorded_requests=reqs) as url:
        plugins_reload("foo", url=url, token="tok", timeout=2.0)
    assert reqs[0]["params"]["scopes"] == ["operator.admin"]


def test_rpc_client_external_operator_identity():
    """Client presents as external operator CLI with engine display name, not internal helper."""
    reqs: list[dict] = []
    with fake_gateway_server(expected_token="tok", recorded_requests=reqs) as url:
        health(url=url, token="tok", timeout=2.0)
    client_info = reqs[0]["params"]["client"]
    assert client_info["id"] == "cli"
    assert client_info["mode"] == "cli"
    assert client_info["displayName"] == "nexgen"
    assert client_info["id"] != "gateway-client"
    assert client_info["mode"] != "backend"


def test_handshake_and_health():
    """Client successfully connects, authenticates and fetches health."""
    with fake_gateway_server(expected_token="tok123") as url:
        client = GatewayRpcClient(url=url, token="tok123", timeout=2.0)
        client.connect()
        assert client.is_connected
        h = client.health()
        assert h["ok"] is True
        assert h["uptimeMs"] == 42000
        assert "nexgen" in h["plugins"]["loaded"]
        client.close()
        assert not client.is_connected


def test_config_get():
    """Client fetches active configuration snapshot and revision hash."""
    with fake_gateway_server(expected_token="tok123", current_hash="rev-42") as url:
        cfg = config_get(url=url, token="tok123", timeout=2.0)
        assert cfg["hash"] == "rev-42"
        assert cfg["config"]["gateway"]["port"] == 18789


def test_config_patch_optimistic_concurrency():
    """config_patch succeeds with matching base_hash and raises GatewayConflictError on stale hash."""
    with fake_gateway_server(expected_token="tok123", current_hash="rev-42") as url:
        # Success with current hash
        res = config_patch({"agents": {"list": []}}, base_hash="rev-42", url=url, token="tok123", timeout=2.0)
        assert res["ok"] is True
        assert res["changedPaths"] == ["agents"]

        # Conflict error with stale hash
        with pytest.raises(GatewayConflictError) as exc_info:
            config_patch({"agents": {"list": []}}, base_hash="rev-stale", url=url, token="tok123", timeout=2.0)
        assert "mismatch" in str(exc_info.value).lower()


def test_auth_token_rejected_does_not_leak_token():
    """Wrong token raises GatewayAuthError and never includes the token value in the error."""
    secret = "super-secret-password-xyz"
    with fake_gateway_server(expected_token="other-token") as url:
        with pytest.raises(GatewayAuthError) as exc_info:
            health(url=url, token=secret, timeout=2.0)
        msg = str(exc_info.value)
        assert secret not in msg
        assert "token rejected" in msg.lower() or "authentication failed" in msg.lower()


def test_protocol_mismatch():
    """Protocol version incompatibility raises GatewayProtocolError."""
    with fake_gateway_server(protocol_error=True) as url:
        with pytest.raises(GatewayProtocolError) as exc_info:
            health(url=url, token="secret-token", timeout=2.0)
        assert "protocol mismatch" in str(exc_info.value).lower()


def test_scope_error():
    """Method rejected for missing scope raises GatewayScopeError."""
    with fake_gateway_server(scope_error=True) as url:
        with pytest.raises(GatewayScopeError) as exc_info:
            health(url=url, token="secret-token", timeout=2.0)
        assert "missing scope" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()


def test_plugins_install_and_reload():
    """plugins_install accepts dict or string source; plugins_reload accepts plugin id."""
    with fake_gateway_server(expected_token="tok123") as url:
        # dict source
        res1 = plugins_install({"source": "npm", "spec": "@nexgen/plugin"}, url=url, token="tok123", timeout=2.0)
        assert res1["installed"] is True
        assert res1["source"] == "npm"

        # string source with prefix
        res2 = plugins_install("clawhub:sample-pkg", url=url, token="tok123", timeout=2.0)
        assert res2["installed"] is True
        assert res2["source"] == "clawhub"

        # reload
        res3 = plugins_reload("nexgen", url=url, token="tok123", timeout=2.0)
        assert res3["ok"] is True
        assert res3["pluginIds"] == ["nexgen"]


def test_gateway_unreachable():
    """Connecting to a closed port raises GatewayConnectionError without raw socket traces."""
    with pytest.raises(GatewayConnectionError) as exc_info:
        health(url="ws://127.0.0.1:59999", token="any", timeout=0.5)
    assert "not reachable" in str(exc_info.value).lower() or "timed out" in str(exc_info.value).lower()


def test_gateway_timeout():
    """Unresponsive gateway raises GatewayTimeoutError."""
    with fake_gateway_server(hang=True) as url:
        with pytest.raises(GatewayTimeoutError) as exc_info:
            health(url=url, token="any", timeout=0.2)
        assert "timed out" in str(exc_info.value).lower()


def test_token_hygiene_and_repr():
    """GatewayRpcClient repr and credentials resolution never print secret tokens."""
    token = "my-secret-token-12345"
    client = GatewayRpcClient(url="ws://127.0.0.1:18789", token=token)
    rep = repr(client)
    assert token not in rep
    assert "127.0.0.1:18789" in rep


def test_resolve_gateway_credentials_from_env_and_config(tmp_path: Path):
    """URL and token resolve cleanly across env vars and openclaw.json."""
    home = tmp_path / "fakehome"
    dot_openclaw = home / ".openclaw"
    dot_openclaw.mkdir(parents=True)
    cfg_file = dot_openclaw / "openclaw.json"
    cfg_file.write_text(json.dumps({
        "gateway": {
            "port": 19999,
            "auth": {"token": "file-token-abc"},
        }
    }), encoding="utf-8")

    # From file
    assert resolve_gateway_url(home=home, env={}) == "ws://127.0.0.1:19999"
    assert resolve_gateway_token(home=home, env={}) == "file-token-abc"

    # From env overrides
    env_override = {
        "OPENCLAW_GATEWAY_URL": "ws://localhost:18789",
        "OPENCLAW_GATEWAY_TOKEN": "env-token-xyz",
    }
    assert resolve_gateway_url(home=home, env=env_override) == "ws://localhost:18789"
    assert resolve_gateway_token(home=home, env=env_override) == "env-token-xyz"


def test_cli_rpc_health_command():
    """nexgen openclaw rpc health command produces human output, json, and handles errors."""
    with fake_gateway_server(expected_token="tok-cli") as url:
        # Default text format
        args = argparse.Namespace(url=url, token="tok-cli", timeout=2.0, json=False)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cmd_rpc_health(args)
        assert code == 0
        text = out.getvalue()
        assert "healthy" in text.lower()
        assert "42000" in text

        # JSON format
        args_json = argparse.Namespace(url=url, token="tok-cli", timeout=2.0, json=True)
        out_json = io.StringIO()
        with redirect_stdout(out_json):
            code_json = cmd_rpc_health(args_json)
        assert code_json == 0
        payload = json.loads(out_json.getvalue())
        assert payload["ok"] is True
        assert payload["uptimeMs"] == 42000

    # Error case: gateway down
    err_args = argparse.Namespace(url="ws://127.0.0.1:59998", token="tok-cli", timeout=0.5, json=False)
    err_out = io.StringIO()
    with redirect_stderr(err_out):
        code_err = cmd_rpc_health(err_args)
    assert code_err == 1
    assert "Error:" in err_out.getvalue()
