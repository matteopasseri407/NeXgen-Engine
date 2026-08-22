"""Test del waiter lazy-mcp: indice, load, forward, gate mutating FAIL-CLOSED.

Kimi review (BLOCKER): il default di classificazione deve essere mutating.
Un tool senza allowlist esplicita richiede conferma, indipendentemente dalle
MCP tool annotations (advisory, da server non fidati). Il fake upstream qui
espone tool SENZA annotations: e' il caso che il gate deve coprire.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LAZY_MCP = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "mcp" / "lazy-mcp.py"

FAKE_SERVER = r"""
import json, sys
TOOLS = [
  {"name": "read_thing", "description": "reads a value", "inputSchema": {"type": "object", "properties": {}}},
  {"name": "write_thing", "description": "writes a value", "inputSchema": {"type": "object", "properties": {"v": {"type": "string"}}}},
]
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    m, rid = req.get("method"), req.get("id")
    if m in ("initialize", "notifications/initialized", "ping"):
        continue
    if m == "server/discover":
        out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "supportedVersions": ["2026-07-28"], "capabilities": {"tools": {"listChanged": False}}}}
    elif m == "tools/list":
        out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": TOOLS}}
    elif m == "tools/call":
        p = req["params"]
        if p["name"] == "write_thing":
            out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "written:" + p["arguments"].get("v", "")}]}}
        else:
            out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "value:42"}]}}
    else:
        out = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "nf"}}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
"""


def _write_manifest(vault: Path, body: str) -> None:
    d = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(body, encoding="utf-8")


def _spawn_waiter(vault: Path, audit: Path):
    env = dict(os.environ, AGENT_VAULT_DATA=str(vault), LAZY_MCP_LOG=str(audit))
    return subprocess.Popen(
        ["python3", str(LAZY_MCP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env=env,
    )


def _rpc(proc, method: str, params=None, rid: int = 1):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    time.sleep(0.4)
    return json.loads(proc.stdout.readline().strip())


def _base_manifest(fake_cmd: str) -> str:
    return f"""
schema_version: 1
retired_servers: []
servers:
  fake:
    lazy: true
    command: python3
    args: ["-c", {json.dumps(fake_cmd)}]
    targets: [claude, codex, antigravity, opencode]
"""


def _start_fake(manifest_fragment: str, tmp_path: Path, audit: Path):
    vault = tmp_path / "vault"
    _write_manifest(vault, manifest_fragment)
    proc = _spawn_waiter(vault, audit)
    return proc


def _call(proc, server: str, tool: str, arguments=None, confirm: bool | None = None, rid: int = 10):
    args = {"server": server, "tool": tool, "arguments": arguments or {}}
    if confirm is not None:
        args["confirm"] = confirm
    r = _rpc(proc, "tools/call", {"name": "lazy_call", "arguments": args}, rid=rid)
    return r["result"]


def test_fail_closed_without_any_annotation(tmp_path: Path):
    """Tool SENZA annotations: senza allowlist, la chiamata e rifiutata (fail-closed)."""
    audit = tmp_path / "audit.jsonl"
    proc = _start_fake(_base_manifest(FAKE_SERVER), tmp_path, audit)
    try:
        # anche un tool dal nome read-* e' mutating di default
        res = _call(proc, "fake", "read_thing")
        assert res.get("isError") is True, res
        assert "requires confirmation" in res["content"][0]["text"]
        res = _call(proc, "fake", "write_thing")
        assert res.get("isError") is True
    finally:
        proc.kill()
    log = audit.read_text(encoding="utf-8")
    # il rifiuto va in audit come refused, mai come call eseguita
    assert '"action": "refused"' in log
    assert '"action": "call"' not in log


def test_readonly_allowlist_passes_without_confirm(tmp_path: Path):
    """Tool allowlistato read-only: passa senza conferma; gli altri no."""
    audit = tmp_path / "audit.jsonl"
    manifest = _base_manifest(FAKE_SERVER).replace(
        "  fake:\n    lazy: true",
        "  fake:\n    lazy: true\n    readonly_tools: [read_thing]",
    )
    proc = _start_fake(manifest, tmp_path, audit)
    try:
        res = _call(proc, "fake", "read_thing")
        assert res.get("isError") is not True, res
        assert res["content"][0]["text"] == "value:42"
        res = _call(proc, "fake", "write_thing")
        assert res.get("isError") is True, "write_thing non allowlistato deve essere rifiutato"
    finally:
        proc.kill()
    log = audit.read_text(encoding="utf-8")
    assert '"action": "call"' in log
    assert '"tool": "read_thing"' in log


def test_mutating_with_confirm_executes_and_audits(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    proc = _start_fake(_base_manifest(FAKE_SERVER), tmp_path, audit)
    try:
        res = _call(proc, "fake", "write_thing", {"v": "ciao"}, confirm=True)
        assert res.get("isError") is not True, res
        assert res["content"][0]["text"] == "written:ciao"
    finally:
        proc.kill()
    log = audit.read_text(encoding="utf-8")
    assert '"confirmed": true' in log


def test_index_lists_servers_and_tools(tmp_path: Path):
    proc = _start_fake(_base_manifest(FAKE_SERVER), tmp_path, tmp_path / "audit.jsonl")
    try:
        r = _rpc(proc, "tools/call", {"name": "lazy_list", "arguments": {}}, rid=2)
        idx = json.loads(r["result"]["content"][0]["text"])
        assert "fake" in idx["servers"]
        names = [t["name"] for t in idx["servers"]["fake"]["tools"]]
        assert names == ["read_thing", "write_thing"]
        r = _rpc(proc, "tools/call", {"name": "lazy_load", "arguments": {"server": "fake", "tool": "write_thing"}}, rid=3)
        assert "inputSchema" in r["result"]["content"][0]["text"]
    finally:
        proc.kill()
