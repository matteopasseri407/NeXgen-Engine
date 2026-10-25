"""mcp_bridge_targets.py — which stdio servers the bridge fronts, and how to start each one.

One job: turn manifest names into spawnable backends. The bridge does not keep its
own list of connectors, because a second list would drift from the manifest the
first time a server is added, and a stale bridge is the quiet failure this engine
spends most of its guard code on.

The resolution is the renderer's, deliberately: the command the bridge starts is
the command OpenClaw would have started on Windows, exactly as the renderer writes
it into `openclaw.json` (`windows:` override applied, placeholders expanded, the
trimming shim wrapped when the entry hides tools). A server the plan mounts
directly in OpenClaw is a server the WSL gateway cannot launch, which is precisely
the set this bridge exists to carry.
"""
from __future__ import annotations

from typing import Any, Iterable

#: The CLI whose placement decides what the bridge fronts: the Windows-side gateway.
TARGET_CLI = "openclaw"


class UnknownBridgeServer(LookupError):
    """A name that is not a stdio connector this machine mounts for OpenClaw."""

    def __init__(self, names: Iterable[str], available: Iterable[str]) -> None:
        self.names = sorted(str(name) for name in names)
        self.available = sorted(str(name) for name in available)
        super().__init__(
            f"not a stdio connector of the {TARGET_CLI} gateway: {', '.join(self.names)}"
            f" (available: {', '.join(self.available) or 'none'})"
        )


def _backend(entry: dict[str, Any]) -> dict[str, Any] | None:
    """One resolved manifest entry as a argv plus the environment it declared."""
    command = str(entry.get("command") or "").strip()
    if not command:
        return None
    args = [str(argument) for argument in (entry.get("args") or [])]
    raw_env = entry.get("env") if isinstance(entry.get("env"), dict) else {}
    env = {str(key): str(value) for key, value in raw_env.items() if str(value)}
    return {"command": [command, *args], "env": env}


def stdio_backends(renderer=None) -> dict[str, dict[str, Any]]:
    """Every stdio connector this machine mounts directly in OpenClaw, ready to spawn.

    HTTP connectors are left out: they already listen somewhere and copying them
    behind a bridge would add a hop and a second place to change a token.
    """
    if renderer is None:
        from nexgen_core.renderer import McpRenderer

        renderer = McpRenderer()
    resolved = renderer.load_resolved_servers(TARGET_CLI)
    backends: dict[str, dict[str, Any]] = {}
    for name, entry in resolved.items():
        if not isinstance(entry, dict) or entry.get("url"):
            continue
        backend = _backend(entry)
        if backend is not None:
            backends[str(name)] = backend
    return backends


def select_backends(names: Iterable[str] | None = None, renderer=None) -> dict[str, dict[str, Any]]:
    """The backends to expose: everything stdio by default, or exactly the names asked for.

    An unknown name raises instead of being skipped: a bridge that quietly omits a
    connector leaves the gateway with a tool set that looks fine and is missing one
    server, which is the kind of gap nobody notices until a call fails.
    """
    backends = stdio_backends(renderer)
    wanted = [str(name) for name in (names or [])]
    if not wanted:
        return backends
    unknown = [name for name in wanted if name not in backends]
    if unknown:
        raise UnknownBridgeServer(unknown, backends)
    return {name: backends[name] for name in wanted}
