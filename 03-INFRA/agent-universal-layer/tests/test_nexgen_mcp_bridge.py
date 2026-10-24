"""The bridge's transport: a bearer token on everything, one path per connector, ids that stay each caller's.

These tests start a real listener on loopback and a real fake MCP server as its
child, because the rules that matter here are the ones a mock cannot show: a
missing `Authorization` header, a POST that opens a notification, a backend that
answers out of order. The fake backend is a Python script, never a connector of
the real vault: no test starts the owner's servers.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_bridge import (  # noqa: E402
    BridgeError,
    detect_wsl_host_ip,
    ensure_bridge_token,
    resolve_listen_address,
)
from nexgen_core.mcp_bridge_server import McpHttpBridgeServer  # noqa: E402

#: A standard-in/standard-out MCP server with one tool that answers slowly, which
#: is what makes an id mix-up visible instead of theoretical.
FAKE_MCP = '''
import json
import sys
import time

print("a banner that is not JSON at all")
sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": "2026-07-28", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "slow_echo", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        arguments = (request.get("params") or {}).get("arguments") or {}
        time.sleep(float(arguments.get("ms", 0)) / 1000.0)
        result = {"content": [{"type": "text", "text": "waited " + str(arguments.get("ms", 0))}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id,
                          "error": {"code": -32601, "message": "not found"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
'''

TOKEN = "test-token-6f0c1a2b3c4d5e6f"


@pytest.fixture(autouse=True)
def never_taskkill(monkeypatch):
    """Closing a fake backend must not run `taskkill` on the machine that runs the suite."""
    def _kill(proc, *, process_group=None, timeout=5.0):
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=timeout)
        except Exception:  # noqa: BLE001 - a test double that will not die must not fail the test
            pass
        return True

    monkeypatch.setattr("nexgen_core.mcp_bridge_channel.force_stop_process_tree", _kill)


@pytest.fixture
def fake_backend(tmp_path: Path) -> dict:
    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_MCP, encoding="utf-8")
    return {"command": [sys.executable, str(script)], "env": {}}


@pytest.fixture
def bridge(fake_backend):
    server = McpHttpBridgeServer(
        ("127.0.0.1", 0),
        token=TOKEN,
        backends={"lazy-mcp": fake_backend, "fake-tool": fake_backend},
        nonce="nonce-of-this-run",
    )
    server.start_in_thread()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield {"server": server, "base": base, "url": f"{base}/mcp"}
    finally:
        server.shutdown_and_close()


def _post(url: str, payload, token: str | None = TOKEN, timeout: float = 10.0):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _get(url: str, token: str | None = TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    request = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}"), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}"), exc.headers


def test_every_path_refuses_a_request_without_the_token(bridge):
    """The token is the only lock this listener has: no path may answer before it."""
    status, payload = _post(bridge["url"], {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=None)
    assert status == 401
    assert payload["error"]["message"] == "Unauthorized"

    status, _ = _post(bridge["url"], {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="wrong")
    assert status == 401

    status, _, _ = _get(f"{bridge['base']}/health", token=None)
    assert status == 401, "even the health probe must prove it holds the token"


def test_tools_list_is_forwarded_and_keeps_the_caller_id(bridge):
    status, payload = _post(bridge["url"], {"jsonrpc": "2.0", "id": "abc-1", "method": "tools/list"})
    assert status == 200
    assert payload["id"] == "abc-1"
    assert payload["result"]["tools"][0]["name"] == "slow_echo"


def test_a_tool_call_is_forwarded_to_the_named_connector(bridge):
    status, payload = _post(
        f"{bridge['base']}/mcp/fake-tool",
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "slow_echo", "arguments": {"ms": 5}}},
    )
    assert status == 200
    assert payload["id"] == 7
    assert payload["result"]["content"][0]["text"] == "waited 5"


def test_two_callers_using_the_same_id_each_get_their_own_answer(bridge):
    """The regression this bridge exists to avoid: one caller's answer handed to another."""
    answers: dict[str, str] = {}

    def call(name: str, ms: int) -> None:
        _, payload = _post(
            bridge["url"],
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "slow_echo", "arguments": {"ms": ms}}},
        )
        answers[name] = payload["result"]["content"][0]["text"]

    first = threading.Thread(target=call, args=("slow", 300))
    first.start()
    time.sleep(0.15)  # the slow call is registered first, and must keep its own slot
    second = threading.Thread(target=call, args=("quick", 10))
    second.start()
    first.join(10)
    second.join(10)

    assert answers == {"slow": "waited 300", "quick": "waited 10"}


def test_a_notification_is_accepted_without_a_reply(bridge):
    status, _ = _post(bridge["url"], {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status == 202


def test_get_on_the_endpoint_is_a_405_because_there_is_no_stream(bridge):
    status, _, headers = _get(bridge["url"])
    assert status == 405
    assert "POST" in headers.get("Allow", "")


def test_a_path_that_names_no_connector_is_a_404(bridge):
    status, _ = _post(
        f"{bridge['base']}/mcp/not-a-connector",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert status == 404


def test_health_names_the_run_and_the_connectors(bridge):
    status, payload, _ = _get(f"{bridge['base']}/health")
    assert status == 200
    assert payload["nonce"] == "nonce-of-this-run"
    assert payload["servers"] == ["fake-tool", "lazy-mcp"]
    assert payload["url"].endswith("/mcp")


def test_shutdown_stops_the_listener(bridge):
    status, _ = _post(f"{bridge['base']}/shutdown", {})
    assert status == 200
    bridge["server"]._thread.join(5)
    assert not bridge["server"]._thread.is_alive(), "the serving loop must return, not keep polling"


def test_a_body_that_is_not_a_request_is_refused(bridge):
    status, payload = _post(bridge["url"], [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    assert status == 400, "a batch is not a single JSON-RPC message"
    assert payload["error"]["code"] == -32600

    status, payload = _post(bridge["url"], {"jsonrpc": "2.0", "id": 1})
    assert status == 400
    assert payload["error"]["message"] == "Missing method"


def test_the_listener_binds_loopback_and_nowhere_else(bridge):
    """`ascolta solo dove deve`: 127.0.0.1, and a URL that says so."""
    assert bridge["server"].server_address[0] == "127.0.0.1"
    assert bridge["server"].get_url().startswith("http://127.0.0.1:")
    wildcard = McpHttpBridgeServer(("127.0.0.1", 0), token=TOKEN, backends={}, client_host="0.0.0.0")
    try:
        assert wildcard.get_url().startswith("http://127.0.0.1:"), "a gateway is never handed 0.0.0.0"
    finally:
        wildcard.server_close()


def test_the_default_address_is_loopback_and_the_wildcard_needs_a_token():
    assert resolve_listen_address() == ("127.0.0.1", "127.0.0.1")
    assert resolve_listen_address(host="localhost") == ("127.0.0.1", "127.0.0.1")
    assert resolve_listen_address(host="192.0.2.10") == ("192.0.2.10", "192.0.2.10")
    with pytest.raises(BridgeError):
        resolve_listen_address(host="0.0.0.0", token="")
    assert resolve_listen_address(host="0.0.0.0", token=TOKEN) == ("0.0.0.0", "127.0.0.1")


def test_the_wsl_address_comes_from_the_interface_and_never_falls_back(monkeypatch):
    """A WSL bind that quietly degraded to loopback would report success and be unreachable."""
    monkeypatch.setattr("nexgen_core.mcp_bridge.detect_wsl_host_ip", lambda: "192.0.2.10")
    assert resolve_listen_address(wsl=True) == ("192.0.2.10", "192.0.2.10")

    monkeypatch.setattr("nexgen_core.mcp_bridge.detect_wsl_host_ip", lambda: None)
    with pytest.raises(BridgeError, match="WSL"):
        resolve_listen_address(wsl=True)


def test_the_wsl_probe_reads_the_first_ipv4_and_ignores_noise(monkeypatch):
    monkeypatch.delenv("NEXGEN_WSL_HOST_IP", raising=False)
    assert detect_wsl_host_ip(runner=lambda argv: "noise\n192.0.2.10\n", is_windows=True) == "192.0.2.10"
    assert detect_wsl_host_ip(runner=lambda argv: "", is_windows=True) is None
    assert detect_wsl_host_ip(runner=lambda argv: "192.0.2.10", is_windows=False) is None, "no PowerShell off Windows"
    monkeypatch.setenv("NEXGEN_WSL_HOST_IP", "192.0.2.11")
    assert detect_wsl_host_ip(runner=lambda argv: "192.0.2.10", is_windows=True) == "192.0.2.11"


def test_the_token_is_generated_once_into_the_deposit(tmp_path: Path, monkeypatch):
    """A token that is not stored changes on every start, and the gateway keeps yesterday's."""
    deposit = tmp_path / "secrets.env"
    monkeypatch.setenv("NEXGEN_SECRETS_ENV", str(deposit))
    monkeypatch.delenv("NEXGEN_MCP_BRIDGE_TOKEN", raising=False)

    token = ensure_bridge_token()
    assert len(token) == 64
    assert deposit.is_file()
    assert f"NEXGEN_MCP_BRIDGE_TOKEN={token}" in deposit.read_text(encoding="utf-8")
    assert ensure_bridge_token() == token

    monkeypatch.setenv("NEXGEN_MCP_BRIDGE_TOKEN", "an-owner-chosen-token")
    assert ensure_bridge_token() == "an-owner-chosen-token", "the environment wins over the deposit"


def test_a_token_that_cannot_be_stored_is_an_error_not_an_ephemeral_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NEXGEN_SECRETS_ENV", str(tmp_path / "secrets.env"))
    monkeypatch.delenv("NEXGEN_MCP_BRIDGE_TOKEN", raising=False)
    monkeypatch.setattr("nexgen_core.deposit_env.read_deposit", lambda: {})
    with pytest.raises(BridgeError):
        ensure_bridge_token()


def test_a_dead_backend_is_a_reply_and_the_next_call_starts_a_fresh_one(tmp_path: Path, monkeypatch):
    """A bridge that has to be restarted by hand is worse than no bridge."""
    dying = tmp_path / "dying.py"
    dying.write_text(
        "import json, os, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'],"
        " 'error': {'code': -32000, 'message': 'bye'}}), flush=True)\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    server = McpHttpBridgeServer(
        ("127.0.0.1", 0),
        token=TOKEN,
        backends={"dies": {"command": [sys.executable, str(dying)], "env": {}}},
    )
    server.start_in_thread()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload = _post(f"{base}/mcp/dies", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert status == 200
        assert payload["error"]["message"] == "bye"
        time.sleep(0.2)  # the child has exited; the request below must start another
        status, payload = _post(f"{base}/mcp/dies", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert status == 200
        assert payload["error"]["message"] == "bye"
    finally:
        server.shutdown_and_close()


def test_a_backend_that_never_answers_ends_in_a_phrase(tmp_path: Path):
    mute = tmp_path / "mute.py"
    mute.write_text("import sys, time\nsys.stdin.read(1)\ntime.sleep(30)\n", encoding="utf-8")
    server = McpHttpBridgeServer(
        ("127.0.0.1", 0),
        token=TOKEN,
        backends={"mute": {"command": [sys.executable, str(mute)], "env": {}}},
    )
    for channel in server.channels.values():
        channel.request_timeout = 0.5
    server.start_in_thread()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload = _post(f"{base}/mcp/mute", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        assert status == 200
        assert payload["error"]["code"] == -32000
        assert "time" in payload["error"]["message"]
    finally:
        server.shutdown_and_close()
