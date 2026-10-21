#!/usr/bin/env python3
"""lazy_frontend.py — the MCP 2.0 stdio frontend: framing, protocol answers, one thread per tool call.

The gateway is mounted in a CLI as a stdio server, so this is the half that speaks to the client: it reads one
JSON-RPC line at a time, answers discovery and the legacy handshake, dispatches `tools/call` on worker threads so
a slow server cannot freeze a ping, and turns any failure into an answer instead of silence. It owns no state
beyond the wire: the waiter is handed to `serve`, and the entry point keeps the single instance.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
from typing import Any

from lazy_server_handle import PROTOCOL_VERSION

SERVER_NAME = "lazy-mcp"
SERVER_VERSION = "0.2.1"
FRAMING = "headers"
SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-03-26", "2024-11-05")
UNSUPPORTED_PROTOCOL_VERSION = -32022
SERVER_CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}

LIST_TTL_MS = 60000
LIST_CACHE_SCOPE = "private"
#: `resultType` is required on every result by 2026-07-28. Older clients pass
#: unknown result fields through, so the same envelope serves both eras.
RESULT_TYPE = "complete"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_CONCURRENT_REQUESTS = int(os.environ.get("LAZY_MCP_MAX_CONCURRENCY", "32"))


def _meta_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "lazy_list",
            "description": "Index of servers behind the lazy proxy: tool names (plus one-line hints) per server, and the mutating flag. Call this first to see what exists; the full index has a token budget and degrades to names only when exceeded.",
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "lazy_load",
            "description": "Load the FULL definition (description + input schema) of one tool into context BEFORE calling it. Mandatory before lazy_call for any tool whose arguments you have not seen in full.",
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
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
            "description": "Forward a call to a lazy server's READ-ONLY tool (the ones the manifest declares read-only). Refuses anything else: those go through lazy_mutate. Always lazy_load first.",
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object", "description": "Arguments per the loaded schema"},
                },
                "required": ["server", "tool", "arguments"],
                "additionalProperties": False,
            },
        },
        {
            "name": "lazy_mutate",
            "description": "Forward a call that may change state, to a lazy server's tool the manifest does not declare read-only. Needs \"confirm\": true after you reviewed the arguments. This is the tool your CLI asks the person about: do not route writes around it.",
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object", "description": "Arguments per the loaded schema"},
                    "confirm": {"type": "boolean", "description": "Must be true"},
                },
                "required": ["server", "tool", "arguments", "confirm"],
                "additionalProperties": False,
            },
        },
    ]


INSTRUCTIONS = (
    "lazy-mcp is the universal on-demand loader for the MCP servers declared "
    "lazy in the manifest. Workflow: 1) lazy_list to see what exists; "
    "2) lazy_load(server, tool) to bring the full schema into context; "
    "3) lazy_call to forward a read-only tool, or lazy_mutate (with "
    "confirm: true) for one that may change state. Every action is audited."
)


def _requested_protocol(req: dict[str, Any]) -> str | None:
    meta = (req.get("params") or {}).get("_meta") or {}
    if isinstance(meta, dict):
        v = meta.get("io.modelcontextprotocol/protocolVersion")
        if isinstance(v, str) and v:
            return v
    return None


_OUT_LOCK = threading.Lock()

#: How many tool calls may be in flight at once (`LAZY_MCP_MAX_CONCURRENCY`). The reader takes
#: the slot before it dispatches, so a request over the cap is refused at once instead of queued.
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)

#: The slot the request this thread is answering owns. It is handed back before the reply leaves,
#: not after, so "the client has read the reply" implies "that capacity is free again".
_REQUEST_SLOT = threading.local()


def _release_request_slot() -> None:
    """Give this thread's request slot back, once: both the reply path and the worker's `finally`
    call it, and only the first call does anything.

    Releasing only after the handler returned let a call sent immediately after a reply find the
    slot still taken: `test_excess_concurrent_calls_get_structured_refusals` saw the slow call
    itself refused with -32000 in CI under `-n auto` (`assert 2 in (3, 4, 5)`). The cap counts
    work in flight, and a request whose reply is already on the wire is not in flight any more.
    """
    if getattr(_REQUEST_SLOT, "held", False):
        _REQUEST_SLOT.held = False
        _SLOTS.release()


def _emit(message: dict[str, Any]) -> None:
    """Replies come from several threads now; one line must never interleave with another.

    The request's slot is given back first: its reply is the last thing its handler does, and a
    client that can read that line must then be able to send the next call without being refused.
    """
    line = json.dumps(message) + "\n"
    with _OUT_LOCK:
        _release_request_slot()
        sys.stdout.write(line)
        sys.stdout.flush()


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
    _emit({"jsonrpc": "2.0", "id": req_id, "result": payload})


def _error(req_id: Any, code: int, message: str, data: Any = None) -> None:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _emit({"jsonrpc": "2.0", "id": req_id, "error": err})


def _handle(req: dict[str, Any], waiter) -> None:
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
            _result(req_id, {"content": [{"type": "text", "text": json.dumps(waiter.index(), ensure_ascii=False)}]})
            return
        if name == "lazy_load":
            res = waiter.load(str(args.get("server", "")), str(args.get("tool", "")))
            if "error" in res:
                _result(req_id, {"isError": True, "content": [{"type": "text", "text": res["error"]}]})
                return
            _result(req_id, {"content": [{"type": "text", "text": json.dumps(res["tool"], ensure_ascii=False)}]})
            return
        if name in ("lazy_call", "lazy_mutate"):
            res = waiter.call(
                str(args.get("server", "")), str(args.get("tool", "")),
                args.get("arguments") or {}, bool(args.get("confirm", False)), via=name,
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


def _respond_request_too_large(raw_line: str) -> None:
    """The request exceeded MAX_REQUEST_BYTES: answer instead of hanging the client."""
    try:
        req = json.loads(raw_line[:65536])
        req_id = req.get("id") if isinstance(req, dict) else None
    except (ValueError, UnicodeError):
        req_id = None
    if req_id is not None:
        _error(req_id, -32600, f"request exceeds {MAX_REQUEST_BYTES} bytes")


def _guarded(req: Any, waiter) -> None:
    """Handles one request; whatever goes wrong becomes an answer, never silence (a client waiting on a
    reply that a dead thread will never send waits for ever)."""
    try:
        _handle(req, waiter)
    except Exception as exc:  # noqa: BLE001 - the loop must survive any single request
        req_id = req.get("id") if isinstance(req, dict) else None
        if req_id is not None:
            _error(req_id, -32603, f"internal error ({type(exc).__name__})")


def _dispatch(req: dict[str, Any], waiter) -> None:
    """Runs a tool call on its own thread. Before, the reader handled each request in turn: a six-second
    call to one server held a ping and a call to another server for six seconds, and a 600-second n8n
    timeout would have frozen every server behind it for ten minutes. A server's own calls stay serial
    (its pipe is), so only unrelated work overlaps. Threads are daemons so a stuck call cannot keep the
    process alive after the client has gone."""
    if not _SLOTS.acquire(blocking=False):
        if req.get("id") is not None:
            _error(req["id"], -32000, f"too many requests in flight (limit {MAX_CONCURRENT_REQUESTS})")
        return

    def run() -> None:
        _REQUEST_SLOT.held = True
        try:
            _guarded(req, waiter)
        finally:
            _release_request_slot()

    threading.Thread(target=run, daemon=True, name="lazy-mcp-request").start()


def serve(waiter) -> int:
    """Read the protocol stream until the client closes it, then stop every server this gateway started."""
    previous = {}

    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGTERM)):
            if signum not in previous:
                previous[signum] = signal.signal(signum, interrupted)
    try:
        if FRAMING == "headers":
            while True:
                line = sys.stdin.readline(MAX_REQUEST_BYTES + 2)
                if not line:
                    break
                if len(line.encode("utf-8", errors="replace")) > MAX_REQUEST_BYTES + 1:
                    _respond_request_too_large(line)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except (ValueError, UnicodeError):  # nexgen: allow reason="a line that is not JSON cannot be answered: the protocol has no id to reply to"
                    continue
                if isinstance(req, dict) and req.get("method") == "tools/call":
                    _dispatch(req, waiter)
                else:
                    _guarded(req, waiter)
        return 0
    finally:
        waiter.shutdown()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
