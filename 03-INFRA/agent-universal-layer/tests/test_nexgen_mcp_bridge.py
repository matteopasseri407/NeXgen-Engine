"""Tests for NeXgen MCP HTTP Bridge (Streamable HTTP + Bearer Auth)."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_bridge import (  # noqa: E402
    DEFAULT_HOST,
    McpHttpBridgeServer,
    StdioProcessChannel,
    ensure_bridge_token,
    resolve_listen_address,
    status_bridge,
    stop_bridge,
)

FAKE_MCP_SCRIPT = """
import sys
import json

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    req_id = req.get("id")
    method = req.get("method")
    if method == "notifications/initialized":
        continue
    if req_id is None:
        continue
    if method == "initialize":
        res = {"protocolVersion": "2026-07-28", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1.0"}}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": res}) + "\\n")
        sys.stdout.flush()
    elif method == "tools/list":
        tools = [{"name": "fake_tool", "description": "A fake tool", "inputSchema": {"type": "object"}}]
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}) + "\\n")
        sys.stdout.flush()
    elif method == "tools/call":
        params = req.get("params") or {}
        args = params.get("arguments") or {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": f"called with {args}"}]}}) + "\\n")
        sys.stdout.flush()
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "not found"}}) + "\\n")
        sys.stdout.flush()
"""


@pytest.fixture
def fake_mcp_cmd(tmp_path: Path) -> list[str]:
    script_path = tmp_path / "fake_mcp.py"
    script_path.write_text(FAKE_MCP_SCRIPT, encoding="utf-8")
    return [sys.executable, str(script_path)]


@pytest.fixture
def running_bridge(fake_mcp_cmd):
    channel = StdioProcessChannel(fake_mcp_cmd)
    token = "test-token-abcdef123456"
    server = McpHttpBridgeServer(("127.0.0.1", 0), token=token, default_channel=channel)
    server.start_in_thread()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        yield {"server": server, "url": url, "token": token, "port": port}
    finally:
        server.shutdown_and_close()


def _http_request(url: str, method: str = "GET", headers: dict[str, str] | None = None, data: bytes | None = None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, resp.headers, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read().decode("utf-8")


def test_bridge_rejects_missing_or_invalid_bearer_token(running_bridge):
    url = running_bridge["url"]
    data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode("utf-8")

    # Missing token
    status, _, body = _http_request(url, method="POST", data=data)
    assert status == 401
    assert "Unauthorized" in body

    # Invalid token
    bad_headers = {"Authorization": "Bearer wrong-token", "Content-Type": "application/json"}
    status, _, body = _http_request(url, method="POST", headers=bad_headers, data=data)
    assert status == 401
    assert "Unauthorized" in body

    # Wrong scheme
    status, _, _ = _http_request(url, method="POST", headers={"Authorization": "Basic 1234"}, data=data)
    assert status == 401


def test_bridge_forwards_tools_list_and_call_with_token(running_bridge):
    url = running_bridge["url"]
    token = running_bridge["token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # tools/list
    data_list = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode("utf-8")
    status, _, body = _http_request(url, method="POST", headers=headers, data=data_list)
    assert status == 200
    doc = json.loads(body)
    assert doc["id"] == 1
    assert doc["result"]["tools"][0]["name"] == "fake_tool"

    # tools/call
    data_call = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "fake_tool", "arguments": {"value": 99}}
    }).encode("utf-8")
    status, _, body = _http_request(url, method="POST", headers=headers, data=data_call)
    assert status == 200
    doc_call = json.loads(body)
    assert doc_call["id"] == 2
    assert "called with {'value': 99}" in doc_call["result"]["content"][0]["text"]


def test_bridge_handles_notifications_with_202(running_bridge):
    url = running_bridge["url"]
    token = running_bridge["token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8")
    status, _, _ = _http_request(url, method="POST", headers=headers, data=notif)
    assert status == 202


def test_bridge_handles_get_with_405(running_bridge):
    url = running_bridge["url"]
    token = running_bridge["token"]
    headers = {"Authorization": f"Bearer {token}"}

    status, resp_headers, _ = _http_request(url, method="GET", headers=headers)
    assert status == 405
    assert "POST" in resp_headers.get("Allow", "")


def test_bridge_listen_address_protection():
    # 0.0.0.0 without token must be refused
    with pytest.raises(ValueError, match="0.0.0.0"):
        resolve_listen_address(host="0.0.0.0", token="")

    # 0.0.0.0 with token is permitted
    bind_h, client_h = resolve_listen_address(host="0.0.0.0", token="valid-tok")
    assert bind_h == "0.0.0.0"
    assert client_h == "127.0.0.1"

    # Default is loopback
    b_def, c_def = resolve_listen_address(host=None)
    assert b_def == DEFAULT_HOST
    assert c_def == DEFAULT_HOST


def test_bridge_token_generation_and_storage(tmp_path: Path, monkeypatch):
    secrets_env = tmp_path / "secrets.env"
    monkeypatch.setenv("NEXGEN_SECRETS_ENV", str(secrets_env))
    monkeypatch.delenv("NEXGEN_MCP_BRIDGE_TOKEN", raising=False)

    token = ensure_bridge_token(home=tmp_path)
    assert len(token) == 64
    assert secrets_env.is_file()
    assert token in secrets_env.read_text(encoding="utf-8")

    # Calling again returns same token without changing file
    token2 = ensure_bridge_token(home=tmp_path)
    assert token2 == token


def test_bridge_status_and_stop_lifecycle(tmp_path: Path):
    # When stopped:
    status_info = status_bridge(home=tmp_path)
    assert status_info["running"] is False

    # Stop when already stopped is a clean no-op:
    assert stop_bridge(home=tmp_path) is False
