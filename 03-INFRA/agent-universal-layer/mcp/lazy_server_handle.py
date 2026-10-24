#!/usr/bin/env python3
"""lazy_server_handle.py — one live connection to a lazy backend server.

A lazy server is nothing until something starts it: a stdio subprocess or an HTTP endpoint, at most one per
gateway, kept alive between calls and stopped when it has been silent too long. This module is that connection:
spawn and provisioning hand-off, the version-bounded JSON-RPC exchange over both transports, the legacy handshake
a FastMCP server insists on, the stderr tail that says why a server died, and the idle stop. It knows nothing
about which servers exist or what the index looks like; its owner hands it a spec and the state it must ask.
"""
from __future__ import annotations

import collections
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from typing import Any, Callable

import lazy_env

PROTOCOL_VERSION = "2026-07-28"
#: Version offered when the waiter has to open the legacy handshake with a
#: stdio server: the `mcp` SDK 1.x (FastMCP) refuses `tools/list` before
#: `initialize`, while the 2026-07-28 contract is stateless and needs none.
LEGACY_PROTOCOL_VERSION = "2025-06-18"
SSE_ACCEPT = "application/json, text/event-stream"
MAX_REPLY_BYTES = 8 * 1024 * 1024
TRANSPORT_ERROR = -32000
INDEX_TTL = int(os.environ.get("LAZY_MCP_INDEX_TTL", "300"))
IDLE_MS = int(os.environ.get("LAZY_MCP_IDLE_MS", "600000"))
STDERR_KEEP_LINES = 40
STDERR_SHOWN_CHARS = 600


class TransportError(RuntimeError):
    """A safe diagnostic, never the raw peer payload or exception message."""


def matching_reply(raw: bytes, rid: int) -> dict[str, Any] | None:
    """The reply to `rid` out of one frame, or None when the frame is a notification, a reverse request or
    another request's answer. A malformed frame is a transport error, never a silent skip."""
    try:
        reply = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise TransportError("invalid JSON response") from exc
    if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
        raise TransportError("invalid JSON-RPC response")
    if "method" in reply or type(reply.get("id")) is not type(rid) or reply["id"] != rid:
        return None  # notification, reverse request, or reply to another request
    if ("result" in reply) == ("error" in reply):
        raise TransportError("response needs exactly one result or error")
    if not isinstance(reply.get("result", reply.get("error")), dict):
        raise TransportError("response result or error must be an object")
    return reply


class ServerHandle:
    """One live connection to a lazy server (stdio subprocess or HTTP)."""

    def __init__(self, name: str, spec: dict[str, Any], *, provisioning: Callable[[], dict[str, str]],
                 credentials, process_helpers: Callable[[], tuple], reply_limit: Callable[[], int],
                 client_info: dict[str, str]):
        self.name = name
        self.spec = spec
        #: What its owner asks of the rest of the gateway: the provisioning step, the credential chain, the
        #: process helpers and the reply limit are all resolved at call time through these.
        self._provisioning = provisioning
        self._credentials = credentials
        self._process_helpers = process_helpers
        self._reply_limit = reply_limit
        self._client_info = client_info
        self.proc: subprocess.Popen | None = None
        self.index: list[dict[str, Any]] | None = None
        self.index_at = 0.0
        self.last_use = time.time()
        # FAIL-CLOSED classification: a tool is mutating unless explicitly
        # allowlisted. MCP tool annotations are advisory and come from
        # untrusted servers, so they are never trusted here; the manifest
        # grants read-only status per server (`readonly: true`) or per tool
        # (`readonly_tools: [...]`). Anything else requires confirmation.
        self.readonly_server = bool(spec.get("readonly"))
        self.readonly_tools = set(spec.get("readonly_tools") or [])
        #: Tools the manifest hides (`tools_deny`) or restricts to (`tools_allow`): never listed, never callable.
        self.tools_deny = set(spec.get("tools_deny") or [])
        self.tools_allow = set(spec.get("tools_allow") or [])
        self.active_calls = 0
        self._init_done = False
        self._rid = 600
        self._rpc_lock = threading.RLock()
        self._modern_protocol = False
        self._transport_failed = False
        self._io_worker: threading.Thread | None = None
        #: The last thing the server's stderr said, and why the last request failed. Both used to be
        #: thrown away (stderr went to DEVNULL), which turned "the server crashed on an import" into
        #: "tool not found" with nothing to say why.
        self._stderr_lines: collections.deque[str] = collections.deque(maxlen=STDERR_KEEP_LINES)
        self._stderr_lock = threading.Lock()
        self._child_env: dict[str, str] = {}
        self.last_error: str | None = None

    def visible(self, tool: str) -> bool:
        if tool in self.tools_deny:
            return False
        return not self.tools_allow or tool in self.tools_allow

    def is_mutating(self, tool: str) -> bool:
        return not (self.readonly_server or tool in self.readonly_tools)

    def _start_stdio(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        if self.proc is not None:
            self.stop()  # descendants may still own pipes after their parent exits
        ctx = self._provisioning()
        if "error" in ctx:
            raise RuntimeError(ctx["error"])
        cmd = [self.spec["command"]] + list(self.spec.get("args", []))
        declared = {str(k): str(v) for k, v in (self.spec.get("env") or {}).items()}
        if ctx:
            cmd = [self._credentials.expand(c, ctx) for c in cmd]
            declared = {k: self._credentials.expand(v, ctx) for k, v in declared.items()}
        env = lazy_env.child_environment(declared)
        if ctx:
            env = {k: self._credentials.expand(v, ctx) for k, v in env.items()}
        self._child_env = env
        is_win = sys.platform == "win32"
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": False,
            "env": env,
        }
        if not is_win:
            kwargs["start_new_session"] = True
        _, command_argv, no_window = self._process_helpers()
        kwargs.update(no_window())
        try:
            argv = command_argv(cmd)
        except RuntimeError as exc:  # the launcher's own refusal: a shim on this machine, not argv values
            raise TransportError(str(exc)) from exc
        self.proc = subprocess.Popen(argv, **kwargs)
        self._process_group = self.proc.pid if not is_win else None
        with self._stderr_lock:
            self._stderr_lines.clear()
        threading.Thread(target=self._drain_stderr, args=(self.proc.stderr,), daemon=True).start()
        # A respawned process has not been initialized: the handshake state
        # belongs to the process, not to the handle.
        self._init_done = False
        self._modern_protocol = False

    def _drain_stderr(self, stream) -> None:
        """Keeps the tail of the child's stderr. A full pipe would block the server, so it is always read."""
        try:
            for raw in iter(lambda: stream.readline(4096), b""):
                text = raw.decode("utf-8", "replace").rstrip()
                if text:
                    with self._stderr_lock:
                        self._stderr_lines.append(text)
        except (OSError, ValueError):  # nexgen: allow reason="the pipe was closed under us: the process is gone"
            pass  # the pipe was closed under us: the process is gone

    def stderr_tail(self) -> str:
        """What the server last said on stderr, redacted and short enough to put in an error."""
        with self._stderr_lock:
            lines = list(self._stderr_lines)
        if not lines:
            return ""
        text = lazy_env.redact(" | ".join(lines[-6:]), self._child_env)
        return text[-STDERR_SHOWN_CHARS:]

    def _request_params(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._modern_protocol or method == "initialize":
            return params
        meta = params.get("_meta")
        return {**params, "_meta": {
            **(meta if isinstance(meta, dict) else {}),
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
        }}

    def _timeout(self, method: str) -> float:
        key = "tool" if method == "tools/call" else "startup"
        timeouts = self.spec.get("timeouts") or {}
        value = timeouts.get(key, 90) if isinstance(timeouts, dict) else None
        if isinstance(value, bool):
            raise TransportError("invalid declared timeout")
        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise TransportError("invalid declared timeout") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise TransportError("invalid declared timeout")
        return timeout

    def _error(self, message: str) -> dict[str, Any]:
        return {"error": {"code": TRANSPORT_ERROR, "message": f"{self.name}: {message}"}}

    def _exchange(self, operation, timeout: float, *, stdio: bool) -> dict[str, Any]:
        """Bound all pipe/socket I/O, including a blocked stdin write.

        The worker never starts a process after cancellation. Provisioning
        retains its own bounded contract; this deadline starts with RPC I/O.
        No tool call is retried when its outcome becomes unknown.
        """
        cancelled = threading.Event()
        deadline = time.monotonic() + timeout
        completed: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

        def run():
            try:
                result = operation(cancelled, deadline)
            except TransportError as exc:
                result = self._error(str(exc))
            except urllib.error.HTTPError as exc:
                result = self._error(f"HTTP {exc.code}")
            except TimeoutError:
                result = self._error("RPC timeout; operation outcome may be unknown")
            except Exception as exc:  # transport boundary, raw diagnostics may carry secrets
                result = self._error(f"transport failed ({type(exc).__name__})")
            completed.put(result)

        worker = threading.Thread(target=run, daemon=True)
        if stdio:
            self._io_worker = worker
        worker.start()
        try:
            result = completed.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            cancelled.set()
            if stdio:
                self.stop()  # kills the process and closes its pipes
            worker.join(timeout=0.2)
            return self._error("RPC timeout; operation outcome may be unknown")
        except BaseException:  # cancellation must also stop in-flight pipe I/O
            cancelled.set()
            if stdio:
                self.stop()
            worker.join(timeout=0.2)
            raise
        finally:
            self.last_use = time.time()
        if stdio and result.get("error", {}).get("code") == TRANSPORT_ERROR:
            self.stop()  # malformed/partial transport cannot poison the next request
        worker.join(timeout=0.2)
        if stdio:
            self._io_worker = None
            if result.get("error", {}).get("code") == TRANSPORT_ERROR and not worker.is_alive():
                self._close_pipes()
        return result

    def _rpc_stdio(self, method: str, params: dict[str, Any], rid: int) -> dict[str, Any]:
        try:
            timeout = self._timeout(method)
            self._start_stdio()
            params = self._request_params(method, params)
            payload = (json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n").encode("utf-8")
        except TransportError as exc:
            return self._error(str(exc))
        except Exception as exc:  # launch/serialization boundary, no raw environment values
            return self._error(f"failed to start request ({type(exc).__name__})")
        proc = self.proc
        assert proc and proc.stdin and proc.stdout

        def exchange(cancelled, deadline):
            proc.stdin.write(payload)
            proc.stdin.flush()
            received = 0
            while not cancelled.is_set() and time.monotonic() < deadline:
                line = proc.stdout.readline(self._reply_limit() + 1)
                if not line:
                    raise TransportError("process output closed before a matching response")
                received += len(line)
                if received > self._reply_limit():
                    raise TransportError("response exceeds output limit")
                if not line.strip():
                    continue
                reply = matching_reply(line, rid)
                if reply is not None:
                    return reply
            raise TransportError("RPC timeout; operation outcome may be unknown")

        return self._exchange(exchange, timeout, stdio=True)

    def _rpc_http(self, method: str, params: dict[str, Any], rid: int) -> dict[str, Any]:
        try:
            timeout = self._timeout(method)
            params = self._request_params(method, params)
            body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode()
            req = urllib.request.Request(self.spec["url"], data=body, headers={
                "Content-Type": "application/json", "Accept": SSE_ACCEPT,
            })
            auth = self.spec.get("auth") or {}
            auth_env = auth.get("env") if isinstance(auth, dict) else None
            if auth_env:
                token = self._credentials.declared(auth_env)
                if not token:
                    return self._error(f"missing credential: {auth_env} is not set in this session's environment")
                req.add_header("Authorization", f"Bearer {token}")
        except TransportError as exc:
            return self._error(str(exc))
        except Exception as exc:  # configuration boundary, URLs/headers stay private
            return self._error(f"invalid HTTP request ({type(exc).__name__})")

        def exchange(cancelled, deadline):
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                sse = "text/event-stream" in resp.headers.get("Content-Type", "").lower()
                pending = bytearray()
                event: list[bytes] = []
                received = 0
                while not cancelled.is_set() and time.monotonic() < deadline:
                    # read1 returns available bytes rather than waiting for
                    # EOF or a full buffer on an open SSE connection.
                    chunk = resp.read1(65536)
                    received += len(chunk)
                    if received > self._reply_limit():
                        raise TransportError("response exceeds output limit")
                    pending.extend(chunk)
                    if sse:
                        while b"\n" in pending:
                            line, _, rest = pending.partition(b"\n")
                            pending = bytearray(rest)
                            line = line.rstrip(b"\r")
                            if line.startswith(b"data:"):
                                event.append(line[5:].lstrip(b" "))
                            elif not line and event:
                                reply = matching_reply(b"\n".join(event), rid)
                                event.clear()
                                if reply is not None:
                                    return reply
                            if cancelled.is_set() or time.monotonic() >= deadline:
                                raise TransportError("RPC timeout; operation outcome may be unknown")
                    if not chunk:
                        if sse:
                            if event:
                                reply = matching_reply(b"\n".join(event), rid)
                                if reply is not None:
                                    return reply
                            raise TransportError("HTTP stream closed before a matching response")
                        reply = matching_reply(bytes(pending), rid)
                        if reply is None:
                            raise TransportError("HTTP response ID does not match request")
                        return reply
            raise TransportError("RPC timeout; operation outcome may be unknown")

        return self._exchange(exchange, timeout, stdio=False)

    def rpc(self, method: str, params: dict[str, Any], rid: int) -> dict[str, Any]:
        with self._rpc_lock:
            if self.spec.get("url"):
                reply = self._rpc_http(method, params, rid)
            else:
                reply = self._rpc_stdio(method, params, rid)
            self._transport_failed = reply.get("error", {}).get("code") == TRANSPORT_ERROR
            if self._transport_failed:
                # Give the child a moment to finish printing why it is dying, then say it.
                if not self.spec.get("url"):
                    time.sleep(0.15)
                tail = self.stderr_tail()
                error = reply["error"]
                if tail:
                    error["message"] = f"{error['message']} | server stderr: {tail}"
                self.last_error = error["message"]
            elif "result" in reply:
                self.last_error = None
            return reply

    def _next_rid(self) -> int:
        self._rid += 1
        return self._rid

    def _notify_stdio(self, method: str, params: dict[str, Any]) -> bool:
        """Bound notification writes too, without waiting for a reply."""
        proc = self.proc
        if not proc or not proc.stdin:
            return False
        payload = (json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode()

        def send(cancelled, deadline):
            proc.stdin.write(payload)
            proc.stdin.flush()
            return {}

        reply = self._exchange(send, self._timeout(method), stdio=True)
        self._transport_failed = reply.get("error", {}).get("code") == TRANSPORT_ERROR
        return "error" not in reply

    def _initialize_stdio(self) -> bool:
        """Open the MCP handshake with a legacy stdio server.

        The stateless 2026-07-28 contract needs no handshake, but servers
        built on the `mcp` SDK 1.x (FastMCP) refuse `tools/list` until
        `initialize` has been honoured, answering "Received request before
        initialization was complete". Sending only the list request left
        them indexed with zero tools, silently unreachable. The handshake
        is idempotent per live process: a respawn resets the state where the
        process is spawned.
        """
        if self._init_done and self.proc is not None and self.proc.poll() is None:
            return True
        resp = self.rpc("initialize", {
            "protocolVersion": LEGACY_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": dict(self._client_info),
        }, self._next_rid())
        if "error" in resp or "result" not in resp:
            return False
        if not self._notify_stdio("notifications/initialized", {}):
            return False
        self._init_done = True
        return True

    def tools_list(self) -> list[dict[str, Any]]:
        with self._rpc_lock:
            return self._tools_list()

    def _tools_list(self) -> list[dict[str, Any]]:
        if self.index is not None and time.time() - self.index_at < INDEX_TTL:
            return self.index
        # The first frame on a connection decides its protocol era
        # (handshake vs 2026-07-28) and cannot be changed later: probing with
        # the version-key envelope first pinned dual-era servers (mcp SDK 2.x,
        # e.g. drive-mcp) to the modern era, where the legacy initialize is
        # then refused and every request needs a complete envelope - the
        # server indexed with zero tools, forever. Bare requests open the
        # handshake era that every server in the fleet speaks (dual-era ones
        # included); the envelope is tried only on a fresh spawn, for a
        # modern-only server.
        resp = self.rpc("tools/list", {}, 900 + zlib.crc32(self.name.encode()) % 100)
        if resp.get("error", {}).get("code") == TRANSPORT_ERROR:
            return []
        if ("error" in resp or "result" not in resp) and not self.spec.get("url"):
            # Handshake server (mcp SDK 1.x, custom servers): it refuses the
            # list until `initialize` has been honoured.
            if self._initialize_stdio():
                resp = self.rpc("tools/list", {}, 900 + zlib.crc32(self.name.encode()) % 100)
            if self._transport_failed:
                return []
        if "error" in resp or "result" not in resp:
            # Modern-only server: respawn so the epoch-defining first frame is
            # a complete 2026-07-28 envelope. Both keys are required;
            # clientCapabilities: {} is the minimal valid value.
            self.stop()
            modern = {"_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            }}
            resp = self.rpc("tools/list", modern, 900 + zlib.crc32(self.name.encode()) % 100)
            if "result" in resp:
                self._modern_protocol = True
        if "error" in resp or "result" not in resp:
            # Error or malformed reply: report no tools WITHOUT caching.
            # Caching the failure would wedge the server as "tool not found"
            # for INDEX_TTL even after it recovers.
            return []
        tools = (resp.get("result") or {}).get("tools")
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            return []  # malformed indexes are not cached as successful emptiness
        self.index = tools
        self.index_at = time.time()
        return tools

    def idle(self) -> bool:
        return bool(self.proc and self.proc.poll() is None and time.time() - self.last_use > IDLE_MS / 1000)

    def stop_if_idle(self) -> None:
        # Index/load handshakes are RPCs too. The sweeper must not kill them
        # just because Waiter.call's activity counter is zero.
        if self._rpc_lock.acquire(blocking=False):
            try:
                if self.idle():
                    self.stop()
            finally:
                self._rpc_lock.release()

    def stop(self) -> None:
        if self.proc is not None:
            force_stop, _, _ = self._process_helpers()
            force_stop(self.proc, process_group=getattr(self, "_process_group", None))
            self._process_group = None
            self._init_done = False
            # Always: a worker stuck in readline only ends when its stream
            # closes, and that stream belongs to the process just stopped.
            self._close_pipes()

    def _close_pipes(self) -> None:
        if self.proc is not None:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:  # nexgen: allow reason="a pipe already closed by the child cannot be closed twice"
                        pass
