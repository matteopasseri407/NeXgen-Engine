"""Real local peers exercise reply correlation, deadlines and pipe cleanup."""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def proxy():
    path = Path(__file__).resolve().parents[1] / "mcp" / "lazy-mcp.py"
    spec = importlib.util.spec_from_file_location("mcp_transport_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stdio(proxy, code, **spec):
    return proxy._ServerHandle("synthetic", {
        "command": sys.executable, "args": ["-u", "-c", code],
        "timeouts": {"startup": 0.15, "tool": 0.15}, **spec,
    })


def _bounded_call(handle, method="tools/call", params=None):
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(handle.rpc, method, params or {}, 42)
    try:
        try:
            return future.result(timeout=1.5)
        except TimeoutError:
            pytest.fail("RPC exceeded its declared deadline while the peer stayed alive")
    finally:
        handle.stop()
        if handle.proc:
            handle.proc.wait(timeout=3)
        pool.shutdown(wait=True)


def test_stdio_ignores_notifications_and_other_request_ids(proxy):
    handle = _stdio(proxy, '''
import json, sys
for line in sys.stdin:
    req = json.loads(line)
    for frame in [
        {"jsonrpc": "2.0", "method": "notifications/message", "params": {}},
        {"jsonrpc": "2.0", "id": req["id"] + 1, "result": {"stale": True}},
        {"jsonrpc": "2.0", "id": req["id"], "result": {"text": "città"}},
    ]:
        print(json.dumps(frame), flush=True)
''', timeouts={"tool": 1})
    assert _bounded_call(handle)["result"] == {"text": "città"}


@pytest.mark.parametrize("method", ["tools/call", "tools/list"])
def test_stdio_silent_peer_obeys_declared_deadline_and_is_reaped(proxy, method):
    handle = _stdio(proxy, "import sys\nfor line in sys.stdin: pass")
    response = _bounded_call(handle, method)
    assert "timeout" in response["error"]["message"].lower()
    assert handle.proc.returncode is not None


def test_stdio_blocked_input_write_obeys_deadline(proxy):
    handle = _stdio(proxy, "import time; time.sleep(30)")
    response = _bounded_call(handle, params={"text": "x" * 2_000_000})
    assert "timeout" in response["error"]["message"].lower()


def test_stdio_partial_frame_obeys_deadline(proxy):
    handle = _stdio(proxy, '''
import sys, time
sys.stdin.readline()
sys.stdout.write('{"jsonrpc": "2.0", "id": 42, "result": ')
sys.stdout.flush()
time.sleep(30)
''')
    assert "timeout" in _bounded_call(handle)["error"]["message"].lower()


def test_stdio_invalid_reply_does_not_echo_provider_payload(proxy):
    secret = "sk-" + "syntheticcredential" * 3
    handle = _stdio(proxy, f"import sys\nfor line in sys.stdin: print({secret!r}, flush=True)")
    response = _bounded_call(handle)
    assert "error" in response
    assert secret not in json.dumps(response)


def test_stdio_reply_size_is_bounded(proxy, monkeypatch):
    monkeypatch.setattr(proxy, "MAX_REPLY_BYTES", 4096, raising=False)
    handle = _stdio(proxy, '''
import sys, json
for line in sys.stdin:
    req = json.loads(line)
    print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"text": "x" * 9000}}), flush=True)
''')
    response = _bounded_call(handle)
    assert "error" in response
    assert "limit" in response["error"]["message"].lower()


def test_modern_only_stdio_negotiation_is_retained_for_tool_calls(proxy):
    handle = _stdio(proxy, '''
import sys, json
for line in sys.stdin:
    req = json.loads(line)
    if "id" not in req:
        continue
    meta = req.get("params", {}).get("_meta", {})
    modern = meta.get("io.modelcontextprotocol/protocolVersion") == "2026-07-28"
    result = {"tools": []} if req["method"] == "tools/list" else {"ok": True}
    frame = {"jsonrpc": "2.0", "id": req["id"]}
    frame.update({"result": result} if modern else {"error": {"code": -32022, "message": "modern envelope required"}})
    print(json.dumps(frame), flush=True)
''', timeouts={"startup": 1, "tool": 1})
    try:
        assert handle.tools_list() == []
        response = handle.rpc("tools/call", {}, 42)
        assert response.get("result") == {"ok": True}, response
    finally:
        handle.stop()


def test_stdio_timeout_stops_descendant_server(proxy):
    code = '''
import sys, json, subprocess
child = subprocess.Popen([sys.executable, "-u", "-c", "import socket, time; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); print(s.getsockname()[1], flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
port = int(child.stdout.readline())
for line in sys.stdin:
    req = json.loads(line)
    if req["id"] == 41:
        print(json.dumps({"jsonrpc": "2.0", "id": 41, "result": {"port": port}}), flush=True)
'''
    handle = _stdio(proxy, code, timeouts={"startup": 1, "tool": 0.2})
    try:
        port = handle.rpc("tools/list", {}, 41)["result"]["port"]
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        response = _bounded_call(handle)
        assert "timeout" in response["error"]["message"].lower()
        with socket.socket() as probe:
            probe.settimeout(0.5)
            assert probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        handle.stop()


def test_tool_without_optional_description_does_not_break_index(proxy, monkeypatch):
    waiter = proxy.Waiter()
    handle = proxy._ServerHandle("synthetic", {})
    handle.index = [{"name": "read_value", "inputSchema": {"type": "object"}}]
    handle.index_at = time.time()
    monkeypatch.setattr(proxy, "_lazy_servers", lambda: {"synthetic": {}})
    monkeypatch.setattr(waiter, "_handle", lambda *_: handle)
    entry = waiter.index()["servers"]["synthetic"]["tools"][0]
    assert entry == {"name": "read_value", "hint": ""}
    waiter.shutdown()


@pytest.mark.parametrize("timeout", [None, False, 0, -1, "nan", "inf", {}])
def test_invalid_deadline_is_refused_before_process_start(proxy, timeout):
    handle = _stdio(proxy, "raise SystemExit(42)", timeouts={"tool": timeout})
    assert "timeout" in handle.rpc("tools/call", {}, 42)["error"]["message"].lower()
    assert handle.proc is None


@pytest.fixture
def http_peer():
    stopped = threading.Event()
    mode = {"value": "sse"}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            if mode["value"] in ("json", "wrong_id"):
                body = json.dumps({"jsonrpc": "2.0", "id": req["id"] + (mode["value"] == "wrong_id"), "result": {"text": "città"}}, ensure_ascii=False).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                return
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if mode["value"] == "sse":
                for frame in [
                    {"jsonrpc": "2.0", "method": "notifications/message", "params": {}},
                    {"jsonrpc": "2.0", "id": req["id"] + 1, "result": {"stale": True}},
                    {"jsonrpc": "2.0", "id": req["id"], "result": {"ok": True}},
                ]:
                    self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
                    self.wfile.flush()
            elif mode["value"] == "drip":
                while not stopped.wait(0.02):
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except OSError:
                        break
            stopped.wait(3)
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", mode
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_http_sse_matches_reply_before_connection_closes(proxy, http_peer):
    url, _ = http_peer
    handle = proxy._ServerHandle("synthetic", {"url": url, "timeouts": {"tool": 0.5}})
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(handle.rpc, "tools/call", {}, 42)
    try:
        response = future.result(timeout=1.5)
        assert response["id"] == 42
        assert response["result"] == {"ok": True}
        assert time.monotonic() - started < 1.5
    finally:
        # The fixture closes the connection; do not wait forever on old code.
        pool.shutdown(wait=False)


@pytest.mark.parametrize("mode", ["json", "wrong_id"])
def test_http_json_reply_id_and_utf8(proxy, http_peer, mode):
    url, peer_mode = http_peer
    peer_mode["value"] = mode
    handle = proxy._ServerHandle("synthetic", {"url": url, "timeouts": {"tool": 1}})
    response = handle.rpc("tools/call", {}, 42)
    if mode == "json":
        assert response["result"] == {"text": "città"}
    else:
        assert "error" in response
        assert "ID" in response["error"]["message"]


@pytest.mark.parametrize("mode", ["silent", "drip"])
def test_http_live_connection_cannot_extend_declared_deadline(proxy, http_peer, mode):
    url, peer_mode = http_peer
    peer_mode["value"] = mode
    handle = proxy._ServerHandle("synthetic", {"url": url, "timeouts": {"tool": 0.15}})
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(handle.rpc, "tools/call", {}, 42)
    try:
        response = future.result(timeout=1.5)
        assert "timeout" in response["error"]["message"].lower()
    finally:
        pool.shutdown(wait=False)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal delivery; Windows tree cleanup tested separately")
@pytest.mark.parametrize("interruption", [signal.SIGTERM, signal.SIGINT])
def test_waiter_signal_cleans_up_owned_server(tmp_path, interruption):
    code = '''
import sys, json, socket, os, time
s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen()
for line in sys.stdin:
    req = json.loads(line)
    print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": json.dumps({"pid": os.getpid(), "port": s.getsockname()[1]})}]}}), flush=True)
time.sleep(30)  # peers may keep serving after their stdin closes
'''
    manifest_dir = tmp_path / "03-INFRA/agent-universal-layer/mcp"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.yaml").write_text(json.dumps({"servers": {"synthetic": {
        "lazy": True, "readonly": True, "command": sys.executable, "args": ["-u", "-c", code],
        "timeouts": {"tool": 1},
    }}}))
    source = Path(__file__).resolve().parents[1] / "mcp/lazy-mcp.py"
    proc = subprocess.Popen([sys.executable, str(source)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, start_new_session=True,
                            env={**os.environ, "AGENT_VAULT_DATA": str(tmp_path), "LAZY_MCP_LOG": str(tmp_path / "audit.jsonl")})
    child = None
    try:
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "lazy_call", "arguments": {"server": "synthetic", "tool": "read", "arguments": {}},
        }}
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            response = json.loads(pool.submit(proc.stdout.readline).result(timeout=3))
        child = json.loads(response["result"]["content"][0]["text"])
        proc.send_signal(interruption)
        proc.wait(timeout=6)
        with socket.socket() as probe:
            probe.settimeout(0.5)
            assert probe.connect_ex(("127.0.0.1", child["port"])) != 0, "server survived waiter termination"
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=3)
        if child is not None:
            with socket.socket() as probe:
                probe.settimeout(0.2)
                if probe.connect_ex(("127.0.0.1", child["port"])) == 0:
                    try:
                        os.killpg(child["pid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
