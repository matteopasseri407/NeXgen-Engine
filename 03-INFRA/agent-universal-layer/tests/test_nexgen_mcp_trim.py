"""The trimming shim, the gateway's reasons, and the machine environment: real processes, tiny fake servers.

What has to hold: a hidden tool is neither listed nor callable, everything else passes through unchanged, a server that
cannot work here says why instead of vanishing, and the same entry is what every CLI is configured to start.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))
TRIM = INFRA / "agent-universal-layer" / "mcp" / "mcp-trim.py"
LAZY = INFRA / "agent-universal-layer" / "mcp" / "lazy-mcp.py"

from nexgen_core import mcp_check  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process semantics")

BACKEND = r"""
import json, os, sys, time
open(os.environ["PIDFILE"], "w").write(str(os.getpid()))
TOOLS = [{"name": n, "description": "d " + n, "inputSchema": {"type": "object", "properties": {}}}
         for n in ("keep_a", "keep_b", "drop_me", "drop_too", "slow")]
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        continue
    if method == "tools/list":
        out = {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        name = req["params"]["name"]
        if name == "slow":
            time.sleep(1.5)
        result = {"content": [{"type": "text", "text": "ran " + name}], "structuredContent": {"tool": name},
                  "isError": name == "keep_b"}
        out = {"jsonrpc": "2.0", "id": rid, "result": result}
    else:
        out = {"jsonrpc": "2.0", "id": rid, "result": {}}
    sys.stdout.write(json.dumps(out) + "\n"); sys.stdout.flush()
"""


class Shim:
    def __init__(self, tmp_path: Path, name="svc", extra_env=None):
        self.proc = subprocess.Popen([sys.executable, str(TRIM), name], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True,
                                     env={**os.environ, "AGENT_VAULT_DATA": str(tmp_path / "vault"), **(extra_env or {})})
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(line) for line in self.proc.stdout], daemon=True).start()
        self.n = 0

    def send(self, method, params=None):
        self.n += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        return self.n

    def read(self, timeout=20):
        return json.loads(self.lines.get(timeout=timeout))

    def ask(self, method, params=None):
        self.send(method, params)
        return self.read()

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def write_manifest(tmp_path, servers, monkeypatch=None):
    vault = tmp_path / "vault"
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")
    return path


def backend(tmp_path, **extra):
    entry = {"transport": "stdio", "command": sys.executable, "args": ["-c", BACKEND], "env": {"PIDFILE": str(tmp_path / "pid")},
             "exposure": "eager", "tools_deny": ["drop_me", "drop_too"]}
    entry.update(extra)
    return entry


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --- the shim ------------------------------------------------------------------------------------------

def test_hidden_tools_are_neither_listed_nor_callable_and_the_rest_passes_through_unchanged(isolated):
    write_manifest(isolated, {"svc": backend(isolated)})
    shim = Shim(isolated)
    try:
        init = shim.ask("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
        shim.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        listed = shim.ask("tools/list")["result"]["tools"]
        assert [t["name"] for t in listed] == ["keep_a", "keep_b", "slow"]
        ok = shim.ask("tools/call", {"name": "keep_a", "arguments": {}})["result"]
        assert ok["content"][0]["text"] == "ran keep_a" and ok["structuredContent"] == {"tool": "keep_a"}
        assert shim.ask("tools/call", {"name": "keep_b", "arguments": {}})["result"]["isError"] is True  # passes through
        hidden = shim.ask("tools/call", {"name": "drop_me", "arguments": {}})
        assert hidden["error"]["code"] == -32602 and "hidden" in hidden["error"]["message"]
        assert shim.ask("ping")["result"] == {}
        assert shim.ask("resources/list")["result"] == {"resources": []}
    finally:
        shim.close()


def test_an_allow_list_keeps_only_what_it_names(isolated):
    write_manifest(isolated, {"svc": backend(isolated, tools_deny=[], tools_allow=["keep_a"])})
    shim = Shim(isolated)
    try:
        shim.ask("initialize", {})
        assert [t["name"] for t in shim.ask("tools/list")["result"]["tools"]] == ["keep_a"]
        assert "error" in shim.ask("tools/call", {"name": "keep_b", "arguments": {}})
    finally:
        shim.close()


def test_a_slow_call_does_not_hold_up_a_ping(isolated):
    write_manifest(isolated, {"svc": backend(isolated)})
    shim = Shim(isolated)
    try:
        shim.ask("initialize", {})
        shim.ask("tools/list")
        slow = shim.send("tools/call", {"name": "slow", "arguments": {}})
        started = time.monotonic()
        shim.send("ping")
        first = shim.read()
        assert first["result"] == {} and time.monotonic() - started < 1.0, "the ping waited for the slow call"
        second = shim.read()
        assert second["id"] == slow and second["result"]["content"][0]["text"] == "ran slow"
    finally:
        shim.close()


def test_when_the_cli_goes_away_the_real_server_goes_with_it(isolated):
    write_manifest(isolated, {"svc": backend(isolated)})
    shim = Shim(isolated)
    shim.ask("initialize", {})
    shim.ask("tools/list")
    pid = int((isolated / "pid").read_text())
    assert alive(pid)
    shim.close()
    for _ in range(50):
        if not alive(pid):
            break
        time.sleep(0.1)
    assert not alive(pid)


def test_a_server_that_cannot_work_here_says_why_instead_of_vanishing(isolated, monkeypatch):
    monkeypatch.delenv("SVC_API_TOKEN", raising=False)
    write_manifest(isolated, {"svc": backend(isolated, env={"PIDFILE": str(isolated / "pid"), "SVC_API_TOKEN": "${SVC_API_TOKEN}"})})
    shim = Shim(isolated)
    try:
        shim.ask("initialize", {})
        assert shim.ask("tools/list")["result"]["tools"] == []
        called = shim.ask("tools/call", {"name": "keep_a", "arguments": {}})["result"]
        assert called["isError"] is True and "SVC_API_TOKEN" in called["content"][0]["text"]
        assert not (isolated / "pid").exists(), "a server that cannot work must not even be started"
    finally:
        shim.close()


def test_an_unknown_server_is_a_clear_exit(isolated):
    write_manifest(isolated, {})
    proc = subprocess.run([sys.executable, str(TRIM), "nope"], capture_output=True, text=True, timeout=30,
                          env={**os.environ, "AGENT_VAULT_DATA": str(isolated / "vault")}, input="")
    assert proc.returncode == 2 and "nope" in proc.stderr


# --- the machine environment ---------------------------------------------------------------------------

def test_a_declared_secret_is_found_in_environment_d_when_the_cli_was_not_started_from_the_session(isolated, monkeypatch):
    monkeypatch.delenv("SVC_API_TOKEN", raising=False)
    conf = isolated / "xdg" / "environment.d"
    conf.mkdir(parents=True)
    (conf / "90-test.conf").write_text("# a comment\nSVC_API_TOKEN=from-the-file\nOTHER='quoted'\n", encoding="utf-8")
    write_manifest(isolated, {"svc": backend(isolated, env={"PIDFILE": str(isolated / "pid"), "SVC_API_TOKEN": "${SVC_API_TOKEN}"})})
    shim = Shim(isolated)
    try:
        shim.ask("initialize", {})
        assert [t["name"] for t in shim.ask("tools/list")["result"]["tools"]] == ["keep_a", "keep_b", "slow"]
    finally:
        shim.close()


def lazy_module(monkeypatch, cli="claude"):
    import importlib.util

    monkeypatch.setenv("LAZY_MCP_CLI", cli)
    spec = importlib.util.spec_from_file_location("lazy_for_trim_tests", LAZY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_gateway_names_the_missing_credential_and_never_leaks_the_file_beyond_what_is_declared(isolated, monkeypatch):
    monkeypatch.delenv("GW_TOKEN", raising=False)
    conf = isolated / "xdg" / "environment.d"
    conf.mkdir(parents=True)
    (conf / "90.conf").write_text("UNDECLARED_SECRET=nope\n", encoding="utf-8")
    write_manifest(isolated, {
        "lazy-mcp": {"tier": "core", "command": "x"},
        "needs": {"exposure": "lazy", "transport": "http", "url": "http://127.0.0.1:9/mcp", "auth": {"env": "GW_TOKEN"}},
        "gated": {"exposure": "lazy", "command": "x", "require_env": "GW_GATE"},
    })
    monkeypatch.setenv("AGENT_VAULT_DATA", str(isolated / "vault"))
    gateway = lazy_module(monkeypatch)
    entries = gateway._lazy_servers()
    assert "GW_TOKEN" in entries["needs"]["_unavailable"]
    assert "GW_GATE" in entries["gated"]["_unavailable"]
    waiter = gateway.Waiter()
    try:
        index = waiter.index()
    finally:
        waiter.shutdown()
    assert index["servers"]["needs"]["unavailable"] is True and "GW_TOKEN" in index["servers"]["needs"]["error"]
    assert "UNDECLARED_SECRET" not in json.dumps(index)
    assert gateway._env_default("UNDECLARED_SECRET") == "nope"  # resolvable on request, but only declared names are asked for


def test_the_gateway_hides_denied_tools_and_refuses_them(isolated, monkeypatch):
    write_manifest(isolated, {"lazy-mcp": {"tier": "core", "command": "x"},
                              "svc": backend(isolated, exposure="lazy", tools_deny=["drop_me", "drop_too"], readonly=True)})
    monkeypatch.setenv("AGENT_VAULT_DATA", str(isolated / "vault"))
    gateway = lazy_module(monkeypatch)
    waiter = gateway.Waiter()
    try:
        names = [t["name"] for t in waiter.index()["servers"]["svc"]["tools"]]
        assert names == ["keep_a", "keep_b", "slow"]
        assert "hidden" in waiter.call("svc", "drop_me", {})["error"]
        assert "hidden" in waiter.load("svc", "drop_me")["error"] or "not found" in waiter.load("svc", "drop_me")["error"]
        assert "result" in waiter.call("svc", "keep_a", {})
    finally:
        waiter.shutdown()


# --- what each CLI is told to start, and what the measurement sees ---------------------------------------

def test_every_dialect_starts_the_shim_under_the_servers_own_name(isolated):
    write_manifest(isolated, {"lazy-mcp": {"tier": "core", "command": "python3"}, "svc": backend(isolated)})
    renderer = McpRenderer(vault_data=isolated / "vault", engine_root=INFRA, home=isolated / "home")
    renderer.render_all(write=True)
    home = isolated / "home"
    claude = json.loads((home / ".claude.json").read_text())["mcpServers"]["svc"]
    antigravity = json.loads((home / ".gemini/antigravity/mcp_config.json").read_text())["mcpServers"]["svc"]
    for entry in (claude, antigravity):
        assert entry["args"][0].endswith("mcp-trim.py") and entry["args"][1] == "svc"
    import tomllib

    codex = tomllib.loads((home / ".codex/config.toml").read_text())["mcp_servers"]["svc"]
    assert codex["args"][0].endswith("mcp-trim.py") and codex["args"][1] == "svc"
    assert "PIDFILE" not in json.dumps(claude), "the real server's environment stays out of the CLI's config"


def test_the_direct_measurement_sees_the_trimmed_server(isolated):
    write_manifest(isolated, {"lazy-mcp": {"tier": "core", "command": sys.executable}, "svc": backend(isolated)})
    renderer = McpRenderer(vault_data=isolated / "vault", engine_root=INFRA, home=isolated / "home")
    outcome = mcp_check.check_direct(renderer, "claude", "svc", timeout=30)
    assert outcome.ok, outcome.problem
    assert outcome.tools == 3  # five tools, two hidden
