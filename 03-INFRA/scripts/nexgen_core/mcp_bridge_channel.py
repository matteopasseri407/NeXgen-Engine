"""mcp_bridge_channel.py — one stdio MCP server, driven over its pipes by the HTTP bridge.

One job: own the child process and the request/reply correlation on its stdin and
stdout. It starts the backend on the first request rather than at bridge start,
because a gateway that mounts five connectors and uses one should not keep five
interpreters resident.

Two rules decide the shape of this file.

Request ids are the bridge's own. Two HTTP callers sharing one endpoint each send
`id: 1` on their first call; forwarding those ids unchanged would let the answer
to one call be handed to the other, and the mistake would look like a flaky
gateway. The channel therefore rewrites every outgoing id and restores the
caller's on the way back.

A dead backend is a phrase, never an exception. Every failure path answers with a
JSON-RPC error the caller can print, and the channel starts a fresh backend on the
next request: a bridge that has to be restarted by hand is worse than no bridge.
"""
from __future__ import annotations

import json
import subprocess
import threading
from typing import Any

from nexgen_core.processes import force_stop_process_tree, no_window_kwargs

#: How long one forwarded request may wait for its reply. Above the slowest tool
#: this engine knows (an OCR call, a browser navigation) so the bridge is never
#: the shorter of two timeouts; the HTTP client still owns its own limit.
DEFAULT_REQUEST_TIMEOUT = 300.0

#: The JSON-RPC code for an internal failure the protocol has no closer name for.
INTERNAL_ERROR = -32000

#: The JSON-RPC code for a body that is not a request at all.
INVALID_REQUEST = -32600


class StdioProcessChannel:
    """A child MCP server plus the lock, the ids and the reader thread that make it callable."""

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self.command = list(command)
        self.env = env
        self.cwd = cwd
        self.request_timeout = request_timeout
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, list[dict[str, Any]]]] = {}
        self._next_id = 0
        self._closed = False

    # -- lifecycle -------------------------------------------------------

    def _ensure_started(self) -> subprocess.Popen:
        """The live child, started now if it was never started or has exited."""
        if self._closed:
            raise RuntimeError("channel is closed")
        proc = self.proc
        if proc is not None and proc.poll() is None:
            return proc
        if proc is not None:
            # Whoever is still waiting on the dead backend is told now, not at
            # their own timeout: the reason is known and it is not "slow".
            self._fail_pending(proc)
            self._reap(proc)
        proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # The backend's diagnostics are its own: a pipe nobody drains fills up
            # and hangs the server on its next log line.
            stderr=subprocess.DEVNULL,
            cwd=self.cwd,
            env=self.env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **no_window_kwargs(),
        )
        self.proc = proc
        threading.Thread(
            target=self._reader_loop, args=(proc,), daemon=True, name="mcp-bridge-reader"
        ).start()
        return proc

    def _reap(self, proc: subprocess.Popen) -> None:
        """Release the pipes of a backend that is gone, so the next start has a clean slate."""
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:  # nexgen: allow reason="a pipe that is already gone is the state this method leaves behind"
                pass

    def alive(self) -> bool:
        """Whether a backend process is running right now."""
        return self.proc is not None and self.proc.poll() is None

    def close(self) -> None:
        """Stop the backend and refuse every later request."""
        self._closed = True
        proc, self.proc = self.proc, None
        if proc is not None:
            force_stop_process_tree(proc, timeout=2.0)
            self._reap(proc)

    # -- correlation -----------------------------------------------------

    def _reader_loop(self, proc: subprocess.Popen) -> None:
        """Hands every reply to the request that is waiting for that id."""
        stream = proc.stdout
        while stream is not None:
            line = stream.readline()
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except ValueError:  # nexgen: allow reason="a banner, a traceback or a debug print on the child's stdout is not a protocol frame"
                continue
            if not isinstance(message, dict) or "id" not in message:
                # A notification from the backend (list changed, log message):
                # the bridge is request/reply, so there is nobody to route it to.
                continue
            with self._lock:
                waiting = self._pending.pop(message.get("id"), None)
            if waiting is not None:
                event, slot = waiting
                slot.append(message)
                event.set()
        self._fail_pending(proc)

    def _fail_pending(self, proc: subprocess.Popen) -> None:
        """Releases every request still waiting when this backend's stdout ends."""
        with self._lock:
            if self.proc is not None and self.proc is not proc:
                return  # a newer backend owns the pending table already
            pending, self._pending = self._pending, {}
        for request_id, (event, slot) in pending.items():
            slot.append(_error(request_id, INTERNAL_ERROR, "the backend process exited"))
            event.set()

    # -- traffic ---------------------------------------------------------

    def send_notification(self, message: dict[str, Any]) -> None:
        """Forwards a one-way message; a failure here has no caller to answer."""
        proc = self._ensure_started()
        with self._lock:
            self._write(proc, message)

    def send_request(self, message: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """Forwards one request and answers with the backend's reply, id restored."""
        original_id = message.get("id")
        proc = self._ensure_started()
        event = threading.Event()
        slot: list[dict[str, Any]] = []
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            outgoing = dict(message, id=request_id)
            self._pending[request_id] = (event, slot)
            self._write(proc, outgoing)

        if not event.wait(self.request_timeout if timeout is None else timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            return _error(original_id, INTERNAL_ERROR, "the backend did not answer in time")
        reply = dict(slot[0]) if slot else _error(original_id, INTERNAL_ERROR, "no reply from the backend")
        reply["id"] = original_id
        return reply

    def _write(self, proc: subprocess.Popen, message: dict[str, Any]) -> None:
        """One JSON-RPC frame on the backend's stdin, while the caller holds the lock."""
        stream = proc.stdin
        if stream is None:
            return
        try:
            stream.write(json.dumps(message, ensure_ascii=False) + "\n")
            stream.flush()
        except (OSError, ValueError):  # nexgen: allow reason="the pipe died between the liveness check and the write; the reader answers whoever is waiting"
            pass


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """A JSON-RPC error reply for one request id."""
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
