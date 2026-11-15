"""One HTTP conversation with the deployed vault-mcp: its health, and the two MCP calls a deploy must survive.

nexgen: one-job reason="speak to the deployed server over HTTP, and keep the MCP SDK out of every command that imports the doctor"
One job: make those three requests and turn anything unexpected into one
readable sentence.

Why not the MCP SDK the engine already depends on: this runs inside `nexgen
doctor` and inside the updater, both of which import in a fraction of a second
today, and a client session here would open a streamable HTTP transport (with
its own event loop, retries and child reaping) to ask a question a single POST
answers. The engine's `mcp_stdio` uses the SDK for the connector it really has
to speak the protocol to; this is the deploy's own probe.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from nexgen_core.errors import NexgenError

#: Per-request timeout. The container answers in milliseconds when it is up; a
#: deploy waits a few seconds at most before saying the probe failed.
REQUEST_TIMEOUT_SECONDS = 5.0

#: The MCP revision this probe speaks. Any server answers the handshake with the
#: revision it prefers, and the field is echoed, never enforced.
PROTOCOL_VERSION = "2025-06-18"

#: The tool every vault-mcp answers, read-only or write-enabled: the probe needs
#: one name that exists in both modes, because a rollback for the wrong reason is
#: worse than no probe.
REQUIRED_TOOL = "read_note"


class ProbeError(NexgenError, RuntimeError):
    """The deployed service did not answer the way the protocol says it must."""


@dataclass(frozen=True)
class Reply:
    """One answer: its JSON body, and the session id a stateful server handed out."""

    body: dict[str, Any]
    session: str | None = None


def _json_body(text: str, content_type: str) -> dict[str, Any]:
    """The JSON in a body that may arrive as JSON or as a server-sent event.

    The stack sets `MCP_JSON_RESPONSE=true`, so plain JSON is what a correct
    deploy gets. An installation that turned it off answers on the same route in
    `text/event-stream`, and reading that as "not JSON" would fail a service that
    is working.
    """
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped.startswith("event:") or stripped.startswith("data:"):
        for line in stripped.splitlines():
            if line.startswith("data:"):
                stripped = line[5:].strip()
                break
    if not stripped:
        return {}
    try:
        loaded = json.loads(stripped)
    except ValueError as exc:
        raise ProbeError(f"the server answered something that is not JSON ({content_type or 'no content type'})") from exc
    return loaded if isinstance(loaded, dict) else {"result": loaded}


def _request(
    url: str,
    *,
    payload: dict[str, Any] | None,
    token: str | None,
    timeout: float,
    session: str | None = None,
) -> Reply:
    """Send one request, with the headers the MCP transport requires."""
    headers = {"Accept": "application/json, text/event-stream"}
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
            returned = response.headers.get("Mcp-Session-Id") or session
    except urllib.error.HTTPError as exc:
        # The status is the whole message: a body here can echo a request the
        # caller built, and none of it is worth a line in a deploy log.
        raise ProbeError(f"{url} answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ProbeError(f"{url} could not be reached ({exc})") from exc
    return Reply(_json_body(text, content_type), returned)


def get_json(url: str, *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> tuple[dict[str, Any] | None, str]:
    """The JSON body of one GET, or `(None, why)` and never an exception.

    The doctor asks a service that may not be running at all: "nothing answered"
    is a reading there, not a failure of the check.
    """
    try:
        return _request(url, payload=None, token=None, timeout=timeout).body, ""
    except ProbeError as exc:
        return None, str(exc)


def initialize(url: str, *, token: str | None, timeout: float) -> str | None:
    """Open the MCP session and return its id (None on a stateless server)."""
    reply = _request(
        url,
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "nexgen-deploy", "version": "1"},
            },
        },
        token=token,
        timeout=timeout,
    )
    result = reply.body.get("result")
    if not isinstance(result, dict) or not result.get("serverInfo"):
        raise ProbeError("the server answered the MCP handshake without identifying itself")
    return reply.session


def tool_names(url: str, *, token: str | None, timeout: float, session: str | None) -> list[str]:
    """The tools the deployed server publishes, by name."""
    reply = _request(
        url,
        payload={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        token=token,
        timeout=timeout,
        session=session,
    )
    result = reply.body.get("result")
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        raise ProbeError("the server answered tools/list without a list of tools")
    names = [str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name")]
    if REQUIRED_TOOL not in names:
        raise ProbeError(f"the server publishes {len(names)} tool(s) and none of them is '{REQUIRED_TOOL}'")
    return names


def probe(url: str, *, token: str | None, timeout: float = REQUEST_TIMEOUT_SECONDS) -> list[str]:
    """The two calls a rebuilt server has to survive: handshake, then its tools.

    A container whose code is broken can still answer a health route, so the
    health check is not the proof a deploy needs. This is: it proves the process
    serves the MCP transport and can list the tools it was built with.
    """
    session = initialize(url, token=token, timeout=timeout)
    # The notification has no answer worth reading; a stateless server may
    # refuse it and that changes nothing about the two calls that matter.
    try:
        _request(
            url,
            payload={"jsonrpc": "2.0", "method": "notifications/initialized"},
            token=token,
            timeout=timeout,
            session=session,
        )
    except ProbeError:  # nexgen: allow reason="the acknowledgement is optional for a stateless server, and tools/list below is the proof the deploy needs"
        pass
    return tool_names(url, token=token, timeout=timeout, session=session)
