"""`nexgen mcp check`: start each CLI's gateway exactly as that CLI would, and ask it what it serves.

`doctor` proves a command exists and a port answers; it never proves that the gateway a CLI is configured to
start actually starts and offers what the plan says. This does: for each CLI it takes the gateway entry the
renderer would write, spawns it with that command, arguments and environment, performs the handshake, checks the
four meta-tools are there, calls `lazy_list`, and compares the served set with the plan. A backend that cannot
start is reported with its reason (the gateway keeps the tail of its stderr), not as an empty list.

It does start the backends (that is how their tools are listed), so it is an explicit command, not part of `doctor`.
"""
from __future__ import annotations

import json
import os
import queue
import urllib.error
import urllib.request
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.mcp_placement import CLIS, GATEWAY, GATEWAY_CLI_ENV, gateway_servers_for
from nexgen_core.processes import force_stop_process_tree, windows_command_argv

META_TOOLS = frozenset({"lazy_list", "lazy_load", "lazy_call", "lazy_mutate"})


@dataclass
class CliResult:
    cli: str
    ok: bool = True
    seconds: float = 0.0
    served: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


class _Session:
    """One stdio MCP connection, newline-delimited JSON, with a deadline on every read."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: str | None = None) -> None:
        self.proc = subprocess.Popen(
            windows_command_argv(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", env=env, cwd=cwd, start_new_session=os.name == "posix",
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._next = 0

    def _pump(self) -> None:
        for line in self.proc.stdout:  # type: ignore[union-attr]
            self._lines.put(line)
        self._lines.put(None)

    def request(self, method: str, params: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        self._next += 1
        rid = self._next
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(t("no answer to {method} within {seconds:g}s", method=method, seconds=timeout))
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError(t("the process ended before answering {method}", method=method))
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == rid:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error'].get('message', message['error'])}")
                return message.get("result") or {}

    def notify(self, method: str) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]

    def handshake(self, timeout: float) -> None:
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "nexgen-mcp-check", "version": "1"}}, timeout)
        self.notify("notifications/initialized")

    def close(self) -> None:
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        force_stop_process_tree(self.proc, process_group=self.proc.pid if os.name == "posix" else None, timeout=5.0)


def _spawn_env(entry_env: dict[str, str]) -> dict[str, str]:
    return {**os.environ, **{str(k): str(v) for k, v in entry_env.items()}}


def check_gateway(renderer, cli: str, *, timeout: float = 90.0) -> CliResult:
    """The gateway as `cli` would start it, against what the plan says it must serve."""
    result = CliResult(cli)
    started = time.monotonic()
    entry = renderer.load_resolved_servers(cli).get(GATEWAY)
    from nexgen_core import mcp_trials
    from nexgen_core.config import load_mcp_manifest

    # Trials are served too (this machine only), so they are part of what the gateway must offer.
    manifest_servers = mcp_trials.overlay(load_mcp_manifest(renderer.manifest_path).get("servers", {}))
    expected = gateway_servers_for(manifest_servers, cli)
    if entry is None:
        if expected:
            result.ok = False
            result.problems.append(t("the gateway is not mounted in {cli}, but {names} are routed behind it", cli=cli, names=", ".join(sorted(expected))))
        return result
    session = None
    try:
        session = _Session([entry.get("command", ""), *entry.get("args", [])], _spawn_env(entry.get("env") or {}))
        session.handshake(min(timeout, 30.0))
        listed = {tool.get("name") for tool in session.request("tools/list", None, min(timeout, 30.0)).get("tools", [])}
        missing = META_TOOLS - listed
        if missing:
            result.problems.append(t("the gateway does not offer {tools}", tools=", ".join(sorted(missing))))
        reply = session.request("tools/call", {"name": "lazy_list", "arguments": {}}, timeout)
        text = (reply.get("content") or [{}])[0].get("text", "{}")
        index = json.loads(text)
        served = index.get("servers", {})
        result.served = sorted(served)
        if index.get("cli") not in (None, cli):
            result.problems.append(t("the gateway thinks it serves {other}, not {cli}", other=index.get("cli"), cli=cli))
        if entry.get("env", {}).get(GATEWAY_CLI_ENV) != cli:
            result.problems.append(t("the rendered gateway entry does not carry {var}={cli}", var=GATEWAY_CLI_ENV, cli=cli))
        for name in sorted(expected - set(served)):
            result.problems.append(t("{name} should be behind the gateway in {cli} and is not listed", name=name, cli=cli))
        for name in sorted(set(served) - expected):
            result.problems.append(t("{name} is served in {cli} but the plan does not route it there", name=name, cli=cli))
        for name, info in served.items():
            if info.get("error"):
                result.problems.append(f"{name}: {info['error']}")
    except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
        result.problems.append(str(exc))
    finally:
        if session is not None:
            session.close()
    result.seconds = round(time.monotonic() - started, 1)
    result.ok = not result.problems
    return result


@dataclass
class DirectResult:
    server: str
    ok: bool = True
    tools: int = 0
    tokens: int = 0
    seconds: float = 0.0
    problem: str = ""


def _estimate_tokens(tools: list[dict[str, Any]]) -> int:
    """What the tool definitions weigh in a prompt: about four characters a token, which is close enough to compare servers."""
    return len(json.dumps(tools, ensure_ascii=False)) // 4


def _http_tools(entry: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    """tools/list from a streamable-HTTP server: JSON or event-stream replies, a session id if it hands one out."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    auth = entry.get("auth")
    var = auth.get("env") if isinstance(auth, dict) else None
    if var:
        token = os.environ.get(var)
        if not token:
            raise RuntimeError(t("needs {var} in the environment", var=var))
        headers["Authorization"] = f"Bearer {token}"

    def post(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        request = urllib.request.Request(entry["url"], data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:  # noqa: S310 - the user's own configured URL
                body = reply.read().decode("utf-8", errors="replace")
                kind = reply.headers.get("Content-Type", "")
                sid = reply.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(str(getattr(exc, "reason", exc))) from exc
        if "text/event-stream" in kind:
            lines = [ln[5:].strip() for ln in body.splitlines() if ln.startswith("data:")]
            body = lines[-1] if lines else "{}"
        message = json.loads(body) if body.strip() else {}
        return message, ({"Mcp-Session-Id": sid} if sid else {})

    _, extra = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "nexgen-mcp-check", "version": "1"}}})
    headers.update(extra)
    tools: list[dict[str, Any]] = []
    cursor = None
    for page in range(2, 12):
        message, _ = post({"jsonrpc": "2.0", "id": page, "method": "tools/list", "params": {"cursor": cursor} if cursor else {}})
        if "error" in message:
            raise RuntimeError(str(message["error"].get("message", message["error"])))
        result = message.get("result") or {}
        tools.extend(result.get("tools", []))
        cursor = result.get("nextCursor")
        if not cursor:
            break
    return tools


def check_direct(renderer, cli: str, name: str, *, timeout: float = 60.0) -> DirectResult:
    """One directly mounted server, started or called the way `cli` would: how many tools, how heavy, how long."""
    entry = renderer.load_resolved_servers(cli)[name]
    result = DirectResult(name)
    started = time.monotonic()
    session = None
    try:
        if entry.get("transport") == "http" or entry.get("url"):
            tools = _http_tools(entry, timeout)
        else:
            session = _Session([entry.get("command", ""), *entry.get("args", [])], _spawn_env(entry.get("env") or {}))
            session.handshake(timeout)
            tools = []
            cursor = None
            for _ in range(10):
                reply = session.request("tools/list", {"cursor": cursor} if cursor else None, timeout)
                tools.extend(reply.get("tools", []))
                cursor = reply.get("nextCursor")
                if not cursor:
                    break
        result.tools, result.tokens = len(tools), _estimate_tokens(tools)
    except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
        result.ok, result.problem = False, str(exc)
    finally:
        if session is not None:
            session.close()
    result.seconds = round(time.monotonic() - started, 1)
    return result


def main_direct(clis: tuple[str, ...] = CLIS, *, timeout: float = 60.0) -> int:
    """Every directly mounted server, once: what each costs a CLI that loads its tools up front."""
    from nexgen_core.renderer import McpRenderer

    renderer = McpRenderer()
    if not renderer.manifest_path.is_file():
        print(t("No MCP manifest at {path}", path=renderer.manifest_path))
        return 1
    measured: dict[str, DirectResult] = {}
    failed = False
    for cli in clis:
        for name in sorted(renderer.load_resolved_servers(cli)):
            if name == GATEWAY or name in measured:
                continue
            measured[name] = check_direct(renderer, cli, name, timeout=timeout)
    for cli in clis:
        names = [n for n in sorted(renderer.load_resolved_servers(cli)) if n in measured and measured[n].ok]
        tools = sum(measured[n].tools for n in names)
        tokens = sum(measured[n].tokens for n in names)
        print(f"{cli}: " + t("{tools} tools, about {tokens} tokens of definitions loaded up front", tools=tools, tokens=tokens))
    for name, outcome in sorted(measured.items()):
        if outcome.ok:
            print(f"  OK   {name}: " + t("{tools} tools, ~{tokens} tokens, {seconds}s", tools=outcome.tools, tokens=outcome.tokens, seconds=outcome.seconds))
        else:
            failed = True
            print(f"  FAIL {name}: {outcome.problem}")
    return 1 if failed else 0


def main(clis: tuple[str, ...] = CLIS, *, timeout: float = 90.0) -> int:
    from nexgen_core.renderer import McpRenderer

    renderer = McpRenderer()
    if not renderer.manifest_path.is_file():
        print(t("No MCP manifest at {path}", path=renderer.manifest_path))
        return 1
    failed = False
    for cli in clis:
        outcome = check_gateway(renderer, cli, timeout=timeout)
        mark = "OK  " if outcome.ok else "FAIL"
        served = ", ".join(outcome.served) or t("(none)")
        print(f"{mark} {cli}: " + t("gateway served {count} server(s) in {seconds}s: {names}", count=len(outcome.served), seconds=outcome.seconds, names=served))
        for problem in outcome.problems:
            print(f"     ! {problem}")
        failed = failed or not outcome.ok
    return 1 if failed else 0
