#!/usr/bin/env python3
"""lazy-mcp.py — the waiter: universal MCP 2.0 index + on-demand load proxy.

Always-mounted MCP server with a tiny schema (three meta-tools). Real
servers declared `lazy: true` in the canonical manifest are NOT mounted in
the CLIs; this proxy exposes their tool names as a compact index, loads a
full tool schema into the model's context on demand, and forwards calls to
the real server. That is the Claude-Code style deferral replicated for CLIs
without native support (opencode, codex, antigravity) — and the foundation
that scales when the manifest grows to tens of servers: the bootstrap pays
three meta-tools, never the schemas.

Protocol: MCP 2.0 (revision 2026-07-28), same envelope as the engine's
reference server (vault_ocr_mcp.py): stateless, version negotiated per
request via `params._meta["io.modelcontextprotocol/protocolVersion]`,
`server/discover` for capabilities, `resultType` on every result, and
`ttlMs`/`cacheScope` on the cacheable ones. Legacy handshake methods are
answered for the CLIs that still open with them.

Contract with the advisory reviews (Kimi, Opus 5):
- the index is built LIVE from each real server's tools/list (TTL-cached
  per session, never baked);
- `lazy_load` is the explicit schema-delivery step BEFORE any `lazy_call`:
  the model writes arguments only after having seen the full definition;
- loading is an availability gate, not a safety gate: servers marked
  `mutating: true` refuse `lazy_call` without `confirm: true`, and every
  action is appended to an audit log;
- the index budget is enforced: over LAZY_MCP_INDEX_MAX_TOKENS the index
  degrades to server granularity (names only) instead of growing forever;
- spawned servers die after LAZY_MCP_IDLE_MS of silence: a bordello of MCP
  servers must not mean a bordello of resident processes.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SERVER_NAME = "lazy-mcp"
SERVER_VERSION = "0.2.0"
FRAMING = "headers"
PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-03-26", "2024-11-05")
UNSUPPORTED_PROTOCOL_VERSION = -32022
SERVER_CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}

INDEX_MAX_TOKENS = int(os.environ.get("LAZY_MCP_INDEX_MAX_TOKENS", "4000"))
INDEX_TTL = int(os.environ.get("LAZY_MCP_INDEX_TTL", "300"))
IDLE_MS = int(os.environ.get("LAZY_MCP_IDLE_MS", "600000"))
LOG_PATH = os.environ.get("LAZY_MCP_LOG") or str(Path.home() / ".local" / "state" / "lazy-mcp-audit.jsonl")
SSE_ACCEPT = "application/json, text/event-stream"
LIST_TTL_MS = 60000
LIST_CACHE_SCOPE = "private"

#: `resultType` is required on every result by 2026-07-28. Older clients pass
#: unknown result fields through, so the same envelope serves both eras.
RESULT_TYPE = "complete"


def _env_default(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _expand_placeholders(text: str, ctx: dict[str, str]) -> str:
    def repl(match):
        name, default = match.group(1), ""
        if ":-" in name:
            name, default = name.split(":-", 1)
        return ctx.get(name, _env_default(name, default))
    return re.sub(r"\$\{([^}]+)\}", repl, text)


def _resolve_manifest() -> dict[str, Any]:
    vault = Path(os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH")
                 or str(Path.home() / "KnowledgeVault"))
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _lazy_servers() -> dict[str, dict[str, Any]]:
    """Manifest servers with `lazy: true` (placeholders expanded, env gates applied)."""
    data = _resolve_manifest()
    ctx = {
        "AGENT_ENGINE_ROOT": os.environ.get("AGENT_ENGINE_ROOT", ""),
        "AGENT_VAULT_DATA": os.environ.get("AGENT_VAULT_DATA", ""),
        "KNOWLEDGE_VAULT_PATH": os.environ.get("KNOWLEDGE_VAULT_PATH", ""),
    }
    out: dict[str, dict[str, Any]] = {}
    for name, srv in (data.get("servers") or {}).items():
        if not isinstance(srv, dict) or not srv.get("lazy"):
            continue
        req = srv.get("require_env")
        if req and not os.environ.get(req):
            continue
        entry = {k: v for k, v in srv.items()}
        if entry.get("command"):
            entry["command"] = _expand_placeholders(str(entry["command"]), ctx)
        if isinstance(entry.get("args"), list):
            entry["args"] = [_expand_placeholders(str(a), ctx) for a in entry["args"]]
        if entry.get("url"):
            entry["url"] = _expand_placeholders(str(entry["url"]), ctx)
        if isinstance(entry.get("env"), dict):
            entry["env"] = {k: _expand_placeholders(str(v), ctx) for k, v in entry["env"].items()}
        out[name] = entry
    return out


class _ServerHandle:
    """One live connection to a lazy server (stdio subprocess or HTTP)."""

    def __init__(self, name: str, spec: dict[str, Any]):
        self.name = name
        self.spec = spec
        self.proc: subprocess.Popen | None = None
        self.index: list[dict[str, Any]] | None = None
        self.index_at = 0.0
        self.last_use = time.time()
        self.mutating = bool(spec.get("mutating"))

    def _start_stdio(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        cmd = [self.spec["command"]] + list(self.spec.get("args", []))
        env = dict(os.environ)
        if self.spec.get("env"):
            env.update(self.spec["env"])
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, start_new_session=True, env=env,
        )

    def _rpc_stdio(self, method: str, params: dict[str, Any], rid: int) -> dict[str, Any]:
        self._start_stdio()
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n"
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()
        deadline = time.time() + 90
        buf = ""
        while time.time() < deadline:
            line = self.proc.stdout.readline() if self.proc.stdout else ""
            if not line:
                time.sleep(0.05)
                continue
            buf = line.strip()
            if buf:
                break
        self.last_use = time.time()
        try:
            return json.loads(buf)
        except Exception:
            return {"error": {"code": -32603, "message": f"invalid response from {self.name}: {buf[:200]}"}}

    def _rpc_http(self, method: str, params: dict[str, Any], rid: int) -> dict[str, Any]:
        url = self.spec["url"]
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "Accept": SSE_ACCEPT,
        })
        auth = self.spec.get("auth") or {}
        auth_env = auth.get("env") if isinstance(auth, dict) else None
        if auth_env and os.environ.get(auth_env):
            req.add_header("Authorization", f"Bearer {os.environ[auth_env]}")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            return {"error": {"code": exc.code, "message": f"{self.name}: HTTP {exc.code}"}}
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                break
        self.last_use = time.time()
        try:
            return json.loads(raw)
        except Exception:
            return {"error": {"code": -32603, "message": f"invalid response from {self.name}"}}

    def rpc(self, method: str, params: dict[str, Any], rid: int) -> dict[str, Any]:
        if self.spec.get("url"):
            return self._rpc_http(method, params, rid)
        return self._rpc_stdio(method, params, rid)

    def tools_list(self) -> list[dict[str, Any]]:
        if self.index is not None and time.time() - self.index_at < INDEX_TTL:
            return self.index
        modern = {"_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION}}
        resp = self.rpc("tools/list", modern, 900 + hash(self.name) % 100)
        if "error" in resp or "result" not in resp:
            resp = self.rpc("tools/list", {}, 900 + hash(self.name) % 100)
        tools = (resp.get("result") or {}).get("tools", [])
        self.index = tools
        self.index_at = time.time()
        return tools

    def idle(self) -> bool:
        return bool(self.proc and self.proc.poll() is None and time.time() - self.last_use > IDLE_MS / 1000)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


class Waiter:
    """Lazy proxy state: per-server handles, index budget, audit, idle sweep."""

    def __init__(self) -> None:
        self.handles: dict[str, _ServerHandle] = {}
        self.loaded: set[tuple[str, str]] = set()
        self._rid = 1000
        self._lock = threading.Lock()
        self._sweeper = threading.Thread(target=self._sweep_loop, daemon=True)
        self._sweeper.start()

    def _sweep_loop(self) -> None:
        while True:
            time.sleep(30)
            with self._lock:
                for name, handle in list(self.handles.items()):
                    if handle.idle():
                        handle.stop()

    def _audit(self, server: str, tool: str, action: str, confirmed: bool = False) -> None:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "server": server, "tool": tool, "action": action,
                    "confirmed": confirmed,
                }) + "\n")
        except OSError:
            pass

    def _handle(self, name: str, spec: dict[str, Any]) -> _ServerHandle:
        with self._lock:
            h = self.handles.get(name)
            if h is None:
                h = _ServerHandle(name, spec)
                self.handles[name] = h
            return h

    def index(self) -> dict[str, Any]:
        servers = _lazy_servers()
        result: dict[str, Any] = {"servers": {}, "budget_tokens": INDEX_MAX_TOKENS}
        estimated = 0
        for name in sorted(servers):
            handle = self._handle(name, servers[name])
            tools = handle.tools_list()
            entries = []
            for t in tools:
                desc = (t.get("description") or "").splitlines()[0][:100]
                entries.append({"name": t.get("name", ""), "hint": desc})
                estimated += len(t.get("name", "")) // 4 + len(desc) // 4 + 2
            result["servers"][name] = {"mutating": handle.mutating, "tools": entries}
        # Budget enforcement: over budget the index degrades to names only
        # (server granularity), so a bordello of servers cannot blow the
        # bootstrap with descriptions.
        if estimated > INDEX_MAX_TOKENS:
            for name in result["servers"]:
                result["servers"][name]["tools"] = [{"name": t["name"]} for t in result["servers"][name]["tools"]]
            result["degraded"] = True
        return result

    def load(self, server: str, tool: str) -> dict[str, Any]:
        servers = _lazy_servers()
        if server not in servers:
            return {"error": f"unknown lazy server: {server}"}
        handle = self._handle(server, servers[server])
        for t in handle.tools_list():
            if t.get("name") == tool:
                self._audit(server, tool, "load")
                with self._lock:
                    self.loaded.add((server, tool))
                return {"tool": t}
        return {"error": f"tool '{tool}' not found on {server}"}

    def call(self, server: str, tool: str, arguments: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
        servers = _lazy_servers()
        if server not in servers:
            return {"error": f"unknown lazy server: {server}"}
        handle = self._handle(server, servers[server])
        with self._lock:
            was_loaded = (server, tool) in self.loaded
        if handle.mutating and not confirm:
            return {"error": (
                f"'{tool}' on '{server}' mutates state and requires explicit confirmation: "
                "call again with \"confirm\": true after reviewing the arguments."
            )}
        resp = handle.rpc("tools/call", {"name": tool, "arguments": arguments}, self._next_rid())
        self._audit(server, tool, "call", confirmed=confirm or not handle.mutating)
        if not was_loaded:
            resp.setdefault("hint", "tool definition was never loaded with lazy_load before this call")
        return resp

    def _next_rid(self) -> int:
        self._rid += 1
        return self._rid


WAITER = Waiter()


def _meta_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "lazy_list",
            "description": "Index of servers behind the lazy proxy: tool names (plus one-line hints) per server, and the mutating flag. Call this first to see what exists; the full index has a token budget and degrades to names only when exceeded.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "lazy_load",
            "description": "Load the FULL definition (description + input schema) of one tool into context BEFORE calling it. Mandatory before lazy_call for any tool whose arguments you have not seen in full.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name from lazy_list"},
                    "tool": {"type": "string", "description": "Tool name from lazy_list"},
                },
                "required": ["server", "tool"],
                "additionalProperties": False,
            },
        },
        {
            "name": "lazy_call",
            "description": "Forward a call to a lazy server. Servers marked mutating refuse without \"confirm\": true. Always lazy_load first, and confirm explicitly when the call changes state.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object", "description": "Arguments per the loaded schema"},
                    "confirm": {"type": "boolean", "description": "Required for mutating servers"},
                },
                "required": ["server", "tool", "arguments"],
                "additionalProperties": False,
            },
        },
    ]


INSTRUCTIONS = (
    "lazy-mcp is the universal on-demand loader for the MCP servers declared "
    "lazy in the manifest. Workflow: 1) lazy_list to see what exists; "
    "2) lazy_load(server, tool) to bring the full schema into context; "
    "3) lazy_call to forward the invocation. Mutating servers require "
    "confirm: true. Every action is audited."
)


def _requested_protocol(req: dict[str, Any]) -> str | None:
    meta = (req.get("params") or {}).get("_meta") or {}
    if isinstance(meta, dict):
        v = meta.get("io.modelcontextprotocol/protocolVersion")
        if isinstance(v, str) and v:
            return v
    return None


def _result(req_id: Any, value: dict[str, Any], *, ttl_ms: int | None = None,
            cache_scope: str | None = None) -> None:
    payload = dict(value)
    payload.setdefault("resultType", RESULT_TYPE)
    meta = dict(payload.get("_meta") or {})
    meta.setdefault("io.modelcontextprotocol/serverInfo",
                    {"name": SERVER_NAME, "version": SERVER_VERSION})
    payload["_meta"] = meta
    if ttl_ms is not None:
        payload.setdefault("ttlMs", ttl_ms)
        payload.setdefault("cacheScope", cache_scope or "private")
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": payload}) + "\n")
    sys.stdout.flush()


def _error(req_id: Any, code: int, message: str, data: Any = None) -> None:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": err}) + "\n")
    sys.stdout.flush()


def _handle(req: dict[str, Any]) -> None:
    method = req.get("method")
    req_id = req.get("id")

    requested = _requested_protocol(req)
    if requested is not None and requested not in SUPPORTED_PROTOCOL_VERSIONS:
        if req_id is not None:
            _error(req_id, UNSUPPORTED_PROTOCOL_VERSION,
                   f"unsupported protocol version: {requested}",
                   {"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "requested": requested})
        return

    if method == "server/discover":
        _result(req_id, {
            "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": SERVER_CAPABILITIES,
            "instructions": INSTRUCTIONS,
        }, ttl_ms=LIST_TTL_MS, cache_scope=LIST_CACHE_SCOPE)
        return
    if method == "initialize":
        protocol = (req.get("params") or {}).get("protocolVersion")
        if protocol not in SUPPORTED_PROTOCOL_VERSIONS:
            protocol = PROTOCOL_VERSION
        _result(req_id, {
            "protocolVersion": protocol,
            "capabilities": SERVER_CAPABILITIES,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return
    if method in ("notifications/initialized",):
        return
    if method == "ping":
        _result(req_id, {})
        return
    if method == "tools/list":
        _result(req_id, {"tools": _meta_tools()}, ttl_ms=LIST_TTL_MS, cache_scope=LIST_CACHE_SCOPE)
        return
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name == "lazy_list":
            _result(req_id, {"content": [{"type": "text", "text": json.dumps(WAITER.index(), ensure_ascii=False)}]})
            return
        if name == "lazy_load":
            res = WAITER.load(str(args.get("server", "")), str(args.get("tool", "")))
            if "error" in res:
                _result(req_id, {"isError": True, "content": [{"type": "text", "text": res["error"]}]})
                return
            _result(req_id, {"content": [{"type": "text", "text": json.dumps(res["tool"], ensure_ascii=False)}]})
            return
        if name == "lazy_call":
            res = WAITER.call(
                str(args.get("server", "")), str(args.get("tool", "")),
                args.get("arguments") or {}, bool(args.get("confirm", False)),
            )
            if isinstance(res, dict) and "error" in res:
                msg = res["error"] if isinstance(res["error"], str) else res["error"].get("message", "error")
                _result(req_id, {"isError": True, "content": [{"type": "text", "text": msg}]})
                return
            content = []
            if isinstance(res, dict) and res.get("result"):
                content = res["result"].get("content") or []
            hint = res.get("hint") if isinstance(res, dict) else None
            if hint:
                content = content + [{"type": "text", "text": f"[hint] {hint}"}]
            _result(req_id, {"content": content})
            return
        _result(req_id, {"isError": True, "content": [{"type": "text", "text": f"unknown tool: {name}"}]})
        return
    _error(req_id, -32601, f"method not found: {method}")


def main() -> int:
    if FRAMING == "headers":
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except Exception:
                continue
            _handle(req)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        for handle in WAITER.handles.values():
            handle.stop()
