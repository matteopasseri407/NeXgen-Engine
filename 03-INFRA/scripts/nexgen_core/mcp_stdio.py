"""mcp_stdio.py — one stdio MCP server, driven from synchronous code through the official SDK.

One job: the mechanism a synchronous caller needs to talk to one stdio MCP server —
spawn it, frame the newline-delimited JSON-RPC, perform the handshake, correlate the
replies and stop it — with the official `mcp` SDK doing all of it.

Two components in this engine are synchronous and both must reach a child this way:
the HTTP bridge (a threaded server) and `nexgen mcp check` (a command). The SDK's
client is async, and anyio requires the task that enters one of its contexts to be the
task that leaves it, so this module runs one event loop in its own thread with one
supervisor task inside it. Callers send a request from their own thread and get the
child's raw result back, or the SDK's exception: deciding what a failure means is the
caller's job, and each caller says it differently.

Nothing here retries, restarts or phrases: `StdioSession.restart` throws the child away
and the next call starts a new one, which is the one lifecycle decision both callers
share.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

import anyio
from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, Implementation, Request
from pydantic import TypeAdapter

from nexgen_core import __version__

__all__ = ["CONNECTION_CLOSED", "MCPError", "STOP_TIMEOUT", "StdioSession", "leaf"]

logger = logging.getLogger("nexgen.mcp.stdio")

#: How long letting go of one child may take. The SDK's teardown is bounded (flush,
#: then a grace period, then the process job), so this watches the whole of it.
STOP_TIMEOUT = 15.0

#: A child's reply, as the raw JSON-RPC result the caller expects. Nothing here knows
#: what a tool result looks like: this layer carries bytes, it does not read them.
_RESULT = TypeAdapter(dict[str, Any])

#: Where the child's own diagnostics go: the caller's stderr is a terminal somebody
#: reads, and a connector's chat log there is noise that looks like a failure.
_CHILD_DIAGNOSTICS = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - one handle for the life of the process

#: Who the child is told it is talking to, in its own protocol handshake.
_CLIENT_INFO = Implementation(name="nexgen-engine", version=__version__)


class _Start:
    """One attempt at a child: the session, and what the attempt had to say.

    A fresh one is made for every start, so a supervisor that is still unwinding can
    never set the ready flag of the attempt that replaced it.
    """

    def __init__(self) -> None:
        self.ready = threading.Event()
        self.failure: list[BaseException] = []
        self.session: ClientSession | None = None
        self.stop: anyio.Event | None = None
        self.abandoned = False


class StdioSession:
    """A child MCP server, reachable from any thread, one request per call."""

    def __init__(self, command: list[str], env: dict[str, str] | None = None, cwd: str | None = None) -> None:
        self.command = list(command)
        self.env = dict(env or {})
        self.cwd = cwd
        #: Held while a child is started, replaced or closed; never while a request waits.
        self._guard = threading.Lock()
        self._closed = False
        self._portal: BlockingPortal | None = None
        self._portal_scope: Any = None
        self._start: _Start | None = None
        self._supervisor: Any = None

    # -- lifecycle -------------------------------------------------------

    def _portal_or_start(self) -> BlockingPortal:
        """The thread that runs the child's event loop, started on first use.

        The caller holds `_guard`: the portal and the child it hosts are one object to
        this class, and two threads must not each start one.
        """
        if self._closed:
            raise RuntimeError("this stdio session is closed")
        if self._portal is None:
            scope = start_blocking_portal(name="mcp-stdio")
            self._portal = scope.__enter__()
            self._portal_scope = scope
        return self._portal

    def _parameters(self) -> StdioServerParameters:
        """How the SDK is told to spawn and read the child."""
        return StdioServerParameters(
            command=self.command[0],
            args=self.command[1:],
            env=self.env or None,
            cwd=self.cwd,
            encoding="utf-8",
            encoding_error_handler="replace",
        )

    async def _supervise(self, start: _Start) -> None:
        """Owns the child for as long as it lives.

        The SDK's client is an async context manager, and the task that enters one must
        be the task that leaves it: this task is therefore the only place a child is
        opened or closed, and requests borrow the session from it. `restart` cannot
        cancel a task from another thread, so a watcher inside the same task group is
        how "let go of this child" also reaches one that is still starting.
        """
        start.stop = anyio.Event()
        if start.abandoned:
            return
        async with anyio.create_task_group() as owner:
            owner.start_soon(_stop_when_asked, start, owner.cancel_scope)
            try:
                async with stdio_client(self._parameters(), errlog=_CHILD_DIAGNOSTICS) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream, client_info=_CLIENT_INFO) as session:
                        await session.initialize()
                        start.session = session
                        start.ready.set()
                        await start.stop.wait()
            except Exception as exc:  # nexgen: allow reason="a backend that cannot start is reported to the caller waiting on start.ready; this task has no caller of its own to raise to"
                start.failure.append(exc)
            finally:
                owner.cancel_scope.cancel()
                start.session = None
                start.ready.set()

    def _begin(self) -> _Start:
        """Starts one child, while the caller holds `_guard` so two threads share one start."""
        portal = self._portal_or_start()
        start = _Start()
        self._start = start
        self._supervisor = portal.start_task_soon(self._supervise, start)
        return start

    def _open(self, timeout: float) -> ClientSession:
        """The live session, with the child started now when it is not running.

        A start already under way is *joined*, never duplicated: two threads that arrive
        together must not leave one child unsupervised, because nothing would ever stop it.
        """
        with self._guard:
            start = self._start
            if start is None or (start.ready.is_set() and start.session is None):
                start = self._begin()
        if not start.ready.wait(timeout):
            raise TimeoutError("the backend did not start in time")
        if start.failure:
            self._forget(start)
            raise leaf(start.failure[0])
        if start.session is None:
            self._forget(start)
            raise RuntimeError("the backend did not start")
        return start.session

    def _forget(self, start: _Start) -> None:
        """Drops a start that failed, so the next call tries again instead of repeating it."""
        with self._guard:
            if self._start is start:
                self._start, self._supervisor = None, None

    def restart(self) -> None:
        """Throws the child away, so the next call starts a new one."""
        with self._guard:
            start, self._start = self._start, None
            supervisor, self._supervisor = self._supervisor, None
            if start is not None:
                start.abandoned = True
        if start is None or self._portal is None:
            return
        if start.stop is not None:
            self._portal.call(start.stop.set)
        if supervisor is not None:
            try:
                supervisor.result(timeout=STOP_TIMEOUT)
            except TimeoutError:  # nexgen: allow reason="a child that did not stop in time is cut when this session's event loop goes down; finishing a restart has no caller to raise to"
                logger.warning("mcp stdio: a backend did not stop within %s seconds", STOP_TIMEOUT)

    def close(self) -> None:
        """Stops the child and refuses every later request.

        The event loop is stopped with its tasks cancelled rather than waited for: a
        request still in flight holds a deadline of up to its own timeout, and whoever
        is closing must not inherit it.
        """
        with self._guard:
            self._closed = True
        self.restart()
        with self._guard:
            scope, portal = self._portal_scope, self._portal
            self._portal_scope = self._portal = None
        if scope is None or portal is None:
            return
        portal.call(portal.stop, True)
        scope.__exit__(None, None, None)

    # -- traffic ---------------------------------------------------------

    async def _forward(
        self, session: ClientSession, method: str, params: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        """Sends one request over a live session and returns the result as the child wrote it."""
        with anyio.fail_after(timeout):
            return await session.send_request(Request[dict[str, Any], str](method=method, params=params), _RESULT)

    def call(self, method: str, params: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        """One request, its raw result, or the exception the SDK raised.

        `initialize` answers with what this session's own handshake negotiated rather
        than going back to the child: the caller's client is the one this engine
        speaks to, and it has to be told the session that exists.
        """
        session = self._open(timeout)
        if method == "initialize":
            result = session.initialize_result
            if result is None:
                raise RuntimeError("the backend did not finish a handshake")
            return result.model_dump(by_alias=True, mode="json", exclude_none=True)
        return self._portal.call(self._forward, session, method, params or {}, timeout)


async def _stop_when_asked(start: _Start, scope: anyio.CancelScope) -> None:
    """Turns "let go of this child" into a cancellation, so a start in progress stops too."""
    assert start.stop is not None
    await start.stop.wait()
    scope.cancel()


def leaf(exc: BaseException) -> BaseException:
    """The reason inside an exception group: anyio reports a task group's failure wrapped."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc
