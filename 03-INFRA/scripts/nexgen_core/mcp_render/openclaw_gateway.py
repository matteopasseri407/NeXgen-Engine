"""The `lazy-mcp` entry OpenClaw mounts for its CLI agents.

One job: build the one `mcp.servers.lazy-mcp` entry from the gateway the renderer
already resolved and the connectors OpenClaw mounts beside it, so the gateway can
spawn those connectors when a `claude-cli` agent asks for one.

Why this entry exists at all: OpenClaw starts Claude Code with
`--strict-mcp-config --mcp-config <its own file>`, so a `claude-cli` agent never reads
`~/.claude.json`. The engine keeps the connectors off OpenClaw's `alwaysLoad` loopback
server (943k tokens measured on one request), and this gateway is the road that
remains. It is a normal `mcp.servers` entry, so OpenClaw's own Tool Search still
defers it.

What the entry carries, and what it must not: the command and arguments the renderer
resolved, `LAZY_MCP_CLI=openclaw` so the gateway knows which plan to serve, and every
variable the served connectors declare, always as a `${NAME}` reference OpenClaw
substitutes at spawn time. `PATH`, `HOME` and their Windows equivalents are left out:
OpenClaw already starts every stdio server with the MCP SDK's default environment,
and a reference here would make the setup copy their current values into the
gateway's `.env`, freezing them for every later start. A value never appears here, and neither do the engine's own paths
(`AGENT_ENGINE_ROOT`, `AGENT_VAULT_DATA`, `KNOWLEDGE_VAULT_PATH`): the gateway
resolves those itself.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.mcp_render.openclaw import ENGINE_RESOLVED_VARS, _REFERENCE_NAME
from nexgen_core.openclaw.setup_service_env import PROCESS_VARS

#: The CLI this entry is written for: the gateway asks the placement rule with it.
SERVED_CLI = "openclaw"

#: What the gateway is told beyond the SDK's default environment.
BASE_ENV: dict[str, str] = {"LAZY_MCP_CLI": SERVED_CLI}

#: Already in the SDK's default environment; a reference would freeze them in `.env`.
INHERITED = PROCESS_VARS


def _collect_references(value: Any, found: set[str]) -> None:
    """Every `${NAME}` inside a string, a list or a map, whatever its shape."""
    if isinstance(value, str):
        found.update(_REFERENCE_NAME.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            _collect_references(item, found)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_references(item, found)


def declared_variables(servers: dict[str, dict[str, Any]]) -> list[str]:
    """The variables a served connector declares: gates, bearer tokens, URLs and references.

    Read from the resolved entries, so what counts is what this machine will really
    mount: `require_env`, `auth.env`, `url_env`, and every `${NAME}` in `env`, `args`
    and `url`. Sorted, so the file does not change order between renders.
    """
    found: set[str] = set()
    for srv in servers.values():
        if not isinstance(srv, dict):
            continue
        for key in ("require_env", "url_env"):
            value = srv.get(key)
            if isinstance(value, str) and value.strip():
                found.add(value.strip())
        auth = srv.get("auth")
        if isinstance(auth, dict) and auth.get("env"):
            found.add(str(auth["env"]))
        _collect_references(srv.get("env"), found)
        _collect_references(srv.get("args"), found)
        _collect_references(srv.get("url"), found)
    return sorted(found)


def gateway_env(servers: dict[str, dict[str, Any]]) -> dict[str, str]:
    """The gateway's environment: its CLI, plus one reference per declared variable."""
    env = dict(BASE_ENV)
    for name in declared_variables(servers):
        if name not in ENGINE_RESOLVED_VARS and name not in INHERITED and name not in env:
            env[name] = f"${{{name}}}"
    return env


def gateway_entry(
    gateway: dict[str, Any],
    served: dict[str, dict[str, Any]],
    *,
    python: bool = False,
) -> dict[str, Any]:
    """One `mcp.servers` entry for the gateway: command, arguments and references only."""
    env = gateway_env(served)
    if python:
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
    return {
        "command": str(gateway.get("command") or ""),
        "args": [str(argument) for argument in gateway.get("args") or []],
        "env": env,
    }
