#!/usr/bin/env python3
"""mcp-trim.py NAME: one MCP server, with the tools the manifest hides taken out.

Some servers ship dozens of tools nobody uses, and every CLI that loads tools up front pays for all of their
definitions in every session. A CLI's own filter would have to be written four times, in four dialects, and
one of them (Claude's) does not exist in its MCP config at all. This is the one filter, the same for every CLI:
the CLI is configured to start this instead of the server; it starts the real server (same command, same
filtered environment, same bearer handling as the gateway), lists only the tools that are not in `tools_deny`
(or are in `tools_allow`), and refuses a call to any other. Tool names, results and errors pass through
unchanged, so a permission written for `mcp__firecrawl__firecrawl_scrape` still means the same thing.

It is used only for a server that declares `tools_deny` or `tools_allow`; the renderer writes the entry.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Any

SERVER_VERSION = "0.1.0"
SUPPORTED = ("2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def _load_gateway():
    spec = importlib.util.spec_from_file_location("lazy_mcp_for_trim", Path(__file__).with_name("lazy-mcp.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OUT = threading.Lock()


def _emit(message: dict[str, Any]) -> None:
    line = json.dumps(message, ensure_ascii=False) + "\n"
    with _OUT:
        sys.stdout.write(line)
        sys.stdout.flush()


def _reply(req_id: Any, result: dict[str, Any]) -> None:
    _emit({"jsonrpc": "2.0", "id": req_id, "result": result})


def _fail(req_id: Any, code: int, message: str) -> None:
    _emit({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


class Trimmed:
    def __init__(self, gateway, name: str, entry: dict[str, Any]) -> None:
        self.name = name
        self.gateway = gateway
        self.unavailable = entry.get("_unavailable")
        self.handle = gateway._ServerHandle(name, entry)
        self._rid = 5000
        self._lock = threading.Lock()

    def next_rid(self) -> int:
        with self._lock:
            self._rid += 1
            return self._rid

    def tools(self) -> list[dict[str, Any]]:
        if self.unavailable:
            return []
        return [t for t in self.handle.tools_list() if self.handle.visible(str(t.get("name", "")))]

    def handle_request(self, req: dict[str, Any]) -> None:
        method, req_id, params = req.get("method"), req.get("id"), req.get("params") or {}
        if method == "initialize":
            protocol = params.get("protocolVersion")
            _reply(req_id, {"protocolVersion": protocol if protocol in SUPPORTED else SUPPORTED[0],
                            "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {"name": self.name, "version": SERVER_VERSION}})
        elif method in ("notifications/initialized", "notifications/cancelled") or (req_id is None):
            return
        elif method == "ping":
            _reply(req_id, {})
        elif method == "tools/list":
            if self.unavailable:
                print(f"[mcp-trim] {self.name}: {self.unavailable}", file=sys.stderr)
            _reply(req_id, {"tools": self.tools()})
        elif method == "tools/call":
            self.call(req_id, params)
        elif method in ("resources/list", "resources/templates/list", "prompts/list"):
            key = "prompts" if method.startswith("prompts") else ("resourceTemplates" if "templates" in method else "resources")
            _reply(req_id, {key: []})
        else:
            _fail(req_id, -32601, f"method not found: {method}")

    def call(self, req_id: Any, params: dict[str, Any]) -> None:
        tool = str(params.get("name", ""))
        if self.unavailable:
            _reply(req_id, {"isError": True, "content": [{"type": "text", "text": f"{self.name} is unavailable: {self.unavailable}"}]})
            return
        if not self.handle.visible(tool):
            _fail(req_id, -32602, f"tool '{tool}' is not available on {self.name} (hidden by the manifest)")
            return
        reply = self.handle.rpc("tools/call", params, self.next_rid())
        if "result" in reply:
            _reply(req_id, reply["result"])
        else:
            error = reply.get("error") or {"code": -32603, "message": "no result"}
            _fail(req_id, int(error.get("code", -32603)), str(error.get("message", "error")))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: mcp-trim.py NAME", file=sys.stderr)
        return 2
    gateway = _load_gateway()
    entry = gateway.served_entry(argv[0])
    if entry is None:
        print(f"[mcp-trim] '{argv[0]}' is not in the manifest (or its entry is unusable)", file=sys.stderr)
        return 2
    trimmed = Trimmed(gateway, argv[0], entry)
    slots = threading.BoundedSemaphore(32)

    def run(req: dict[str, Any]) -> None:
        try:
            trimmed.handle_request(req)
        except Exception as exc:  # noqa: BLE001 - one bad request must become an answer, never a silent thread
            if req.get("id") is not None:
                _fail(req["id"], -32603, f"internal error ({type(exc).__name__})")
        finally:
            slots.release()

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                continue
            if not isinstance(req, dict):
                continue
            if req.get("method") == "tools/call":
                # Off the reader, so a slow call does not hold up a ping (the server's own calls stay serial).
                if slots.acquire(blocking=False):
                    threading.Thread(target=run, args=(req,), daemon=True).start()
                elif req.get("id") is not None:
                    _fail(req["id"], -32000, "too many requests in flight")
            else:
                trimmed.handle_request(req)
    finally:
        trimmed.handle.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
