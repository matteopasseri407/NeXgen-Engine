#!/usr/bin/env python3
"""lazy_waiter.py — the gateway's live state: who is connected, what was called, when to stop.

One object owns the per-server handles, keeps the index inside its token budget, appends every action to the
audit log and sweeps backends that have been silent too long. It never resolves the manifest or opens a
connection itself: its owner hands it the resolver, the handle factory and the audit path, so the entry point
stays the only place that knows where the vault, the state directory and the credentials live.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Callable

import lazy_manifest
import lazy_server_handle

INDEX_MAX_TOKENS = int(os.environ.get("LAZY_MCP_INDEX_MAX_TOKENS", "4000"))

#: MCP tool annotations, advisory only (a server can claim read-only and
#: still mutate): they are surfaced to help the caller choose, never to
#: replace this proxy's own fail-closed confirmation gate.
HINT_KEYS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

#: Request threads append to the same audit file: one line at a time, one lock per process.
AUDIT_LOCK = threading.Lock()


class Waiter:
    """Lazy proxy state: per-server handles, index budget, audit, idle sweep."""

    def __init__(self, *, servers: Callable[[], dict[str, dict[str, Any]]],
                 new_handle: Callable[[str, dict[str, Any]], lazy_server_handle.ServerHandle],
                 audit_path: Callable[[], str]) -> None:
        self._servers = servers
        self._new_handle = new_handle
        self._audit_path = audit_path
        self.handles: dict[str, lazy_server_handle.ServerHandle] = {}
        self.loaded: set[tuple[str, str]] = set()
        self._rid = 1000
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep_loop, daemon=True)
        self._sweeper.start()

    def _sweep_loop(self) -> None:
        while not self._shutdown.wait(30):
            with self._lock:
                for name, handle in list(self.handles.items()):
                    if handle.active_calls == 0:
                        handle.stop_if_idle()

    def shutdown(self) -> None:
        """Request all owned process stops before waiting for any one server."""
        self._shutdown.set()
        with self._lock:
            handles = list(self.handles.values())
        workers = []

        def stop(handle):
            try:
                handle.stop()
            except Exception as exc:  # cleanup boundary, do not leak config/URLs
                print(f"[lazy-mcp] cleanup failed for {handle.name} ({type(exc).__name__})", file=sys.stderr)

        for handle in handles:
            worker = threading.Thread(target=stop, args=(handle,), daemon=True)
            worker.start()
            workers.append(worker)
        deadline = time.monotonic() + 5.0
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))

    def _audit(self, server: str, tool: str, action: str, confirmed: bool = False) -> None:
        try:
            with AUDIT_LOCK:
                with open(self._audit_path(), "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "server": server, "tool": tool, "action": action,
                        "confirmed": confirmed,
                    }) + "\n")
        except OSError:  # nexgen: allow reason="an unwritable audit log must never fail the tool call it records"
            pass

    def _handle(self, name: str, spec: dict[str, Any]) -> lazy_server_handle.ServerHandle:
        with self._lock:
            h = self.handles.get(name)
            if h is None:
                h = self._new_handle(name, spec)
                self.handles[name] = h
                return h
            if h.spec != spec:
                # Manifest changed under a live waiter (command/args/env/
                # url/readonly/deps): the old handle would serve the stale
                # config until process exit. Swap it; the old proc dies.
                try:
                    h.stop()
                except Exception:  # nexgen: allow reason="the old handle is being replaced; its failure cannot block the swap"
                    pass
                h = self._new_handle(name, spec)
                self.handles[name] = h
                # A dropped spec may have renamed the tool surface: prior
                # loads could authorize args against a stale schema.
                self.loaded = {(s, t) for (s, t) in self.loaded if s != name}
            return h

    def index(self) -> dict[str, Any]:
        servers = self._servers()
        result: dict[str, Any] = {"servers": {}, "budget_tokens": INDEX_MAX_TOKENS,
                                  "cli": lazy_manifest.served_cli() or None}
        estimated = 0
        for name in sorted(servers):
            if servers[name].get("_unavailable"):
                # Not started: it cannot work in this session, and the reason is the useful part.
                result["servers"][name] = {"mutating": True, "tools": [], "error": servers[name]["_unavailable"],
                                           "unavailable": True}
                continue
            handle = self._handle(name, servers[name])
            tools = [t for t in handle.tools_list() if handle.visible(t.get("name", ""))]
            entries = []
            for t in tools:
                desc = (t.get("description") or "").split("\n", 1)[0][:100]
                entry: dict[str, Any] = {"name": t.get("name", ""), "hint": desc}
                annotations = t.get("annotations")
                if isinstance(annotations, dict):
                    hints = {
                        k: bool(annotations[k])
                        for k in HINT_KEYS
                        if k in annotations and isinstance(annotations[k], bool)
                    }
                    if hints:
                        entry["annotations"] = hints
                entries.append(entry)
                estimated += len(t.get("name", "")) // 4 + len(desc) // 4 + 2
            result["servers"][name] = {"mutating": not handle.readonly_server, "tools": entries}
            if not entries and handle.last_error:
                # An empty list used to be all there was, and "no tools" read as "nothing to offer".
                result["servers"][name]["error"] = handle.last_error
        # Budget enforcement: over budget the index degrades to names only
        # (server granularity), so a bordello of servers cannot blow the
        # bootstrap with descriptions.
        if estimated > INDEX_MAX_TOKENS:
            for name in result["servers"]:
                result["servers"][name]["tools"] = [{"name": t["name"]} for t in result["servers"][name]["tools"]]
            result["degraded"] = True
        return result

    def load(self, server: str, tool: str) -> dict[str, Any]:
        servers = self._servers()
        if server not in servers:
            return {"error": f"unknown lazy server: {server}"}
        if servers[server].get("_unavailable"):
            return {"error": f"server '{server}' is unavailable: {servers[server]['_unavailable']}"}
        handle = self._handle(server, servers[server])
        tools = [t for t in handle.tools_list() if handle.visible(t.get("name", ""))]
        for t in tools:
            if t.get("name") == tool:
                self._audit(server, tool, "load")
                with self._lock:
                    self.loaded.add((server, tool))
                return {"tool": t}
        if not tools and handle.last_error:
            # The server never answered: that is not "tool not found", and the cause is the useful part.
            return {"error": f"server '{server}' is unavailable: {handle.last_error}"}
        return {"error": f"tool '{tool}' not found on {server}"}

    def call(
        self, server: str, tool: str, arguments: dict[str, Any], confirm: bool = False, *, via: str = "lazy_call",
    ) -> dict[str, Any]:
        servers = self._servers()
        if server not in servers:
            return {"error": f"unknown lazy server: {server}"}
        if servers[server].get("_unavailable"):
            return {"error": f"server '{server}' is unavailable: {servers[server]['_unavailable']}"}
        handle = self._handle(server, servers[server])
        if not handle.visible(tool):
            self._audit(server, tool, "refused", confirmed=False)
            return {"error": f"tool '{tool}' on '{server}' is hidden by the manifest (tools_deny / tools_allow)"}
        with self._lock:
            was_loaded = (server, tool) in self.loaded
        if handle.is_mutating(tool):
            if via != "lazy_mutate":
                # A tool the manifest does not declare read-only goes through its own tool, so the CLI's
                # permission for lazy_call can be granted for reads without granting every write.
                self._audit(server, tool, "refused", confirmed=False)
                return {"error": (
                    f"'{tool}' on '{server}' is not explicitly read-only and requires "
                    "confirmation: call lazy_mutate with \"confirm\": true after reviewing "
                    "the arguments. (Read-only status is granted per server or tool "
                    "in the manifest; the default is fail-closed. lazy_mutate is the tool "
                    "your CLI asks the person about.)"
                )}
            if not confirm:
                self._audit(server, tool, "refused", confirmed=False)
                return {"error": f"'{tool}' on '{server}' changes state: call again with \"confirm\": true."}
        with self._lock:
            handle.active_calls += 1
        try:
            resp = handle.rpc("tools/call", {"name": tool, "arguments": arguments}, self._next_rid())
        finally:
            with self._lock:
                handle.active_calls -= 1
        self._audit(server, tool, "call", confirmed=confirm or not handle.is_mutating(tool))
        if not was_loaded:
            resp.setdefault("hint", "tool definition was never loaded with lazy_load before this call")
        return resp

    def _next_rid(self) -> int:
        self._rid += 1
        return self._rid
