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
    from nexgen_core.config import load_mcp_manifest

    expected = gateway_servers_for(load_mcp_manifest(renderer.manifest_path).get("servers", {}), cli)
    # Servers whose environment gate is closed in this shell are withheld by the gateway too.
    manifest_servers = load_mcp_manifest(renderer.manifest_path).get("servers", {})
    expected = {n for n in expected if not (manifest_servers[n].get("require_env") and not os.environ.get(manifest_servers[n]["require_env"]))}
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
