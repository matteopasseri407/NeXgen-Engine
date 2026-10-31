"""Machine-local state and runtime overrides for MCP servers.

Operational policy: manifest.yaml is tracked in the engine repository or Vault
and must never be modified at runtime. Local enablement or machine-specific
server state is kept in the machine's local state directory (mcp-state.json).

The same file holds the engine's memory of the server names it last rendered:
that is what lets a connector removed from the manifest leave every CLI even
when nobody added a tombstone, without claiming names a person added by hand.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from nexgen_core.files import write_private_text
from nexgen_core.paths import resolve_state_dir

FILENAME = "mcp-state.json"

#: Key of the memory: the manifest-declared names seen at the last real render.
DECLARED_KEY = "declared_servers"


def mcp_state_path(home: Path | None = None) -> Path:
    """Return the path to the machine-local MCP state file."""
    return resolve_state_dir(home=home) / FILENAME


def load_mcp_state(home: Path | None = None) -> dict[str, Any]:
    """Load the machine-local MCP state. Missing or invalid file returns empty dict."""
    path = mcp_state_path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):  # nexgen: allow reason="missing or unreadable state returns empty fallback"
        return {}


def save_mcp_state(state: dict[str, Any], home: Path | None = None) -> None:
    """Save the machine-local MCP state with restricted permissions."""
    path = mcp_state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    write_private_text(path, payload)


def load_declared_servers(home: Path | None = None) -> set[str]:
    """The server names the engine declared at its last real render.

    Read by the renderer to retire a connector the manifest no longer
    declares: the engine knows what it wrote, so nobody has to remember a
    tombstone. A missing, unreadable or malformed record is simply no memory.
    """
    names = load_mcp_state(home).get(DECLARED_KEY)
    if not isinstance(names, list):
        return set()
    return {str(name).strip() for name in names if str(name).strip()}


def record_declared_servers(names: Iterable[str], home: Path | None = None) -> None:
    """Adds the names the render that just ran declared to the memory.

    Cumulative on purpose: a CLI whose render failed, or whose config did not
    exist yet, when a server left the manifest still has to drop it later, so
    a name the engine once declared is never forgotten. It is live again only
    while the manifest declares it.
    """
    state = load_mcp_state(home)
    state[DECLARED_KEY] = sorted(load_declared_servers(home) | {str(name) for name in names})
    save_mcp_state(state, home)


def enable_server(name: str, enabled: bool = True, home: Path | None = None) -> None:
    """Enable or disable a named MCP server in machine-local state."""
    state = load_mcp_state(home)
    servers = state.setdefault("servers", {})
    if not isinstance(servers, dict):
        servers = {}
        state["servers"] = servers
    srv = servers.setdefault(name, {})
    if not isinstance(srv, dict):
        srv = {}
        servers[name] = srv
    srv["enabled"] = bool(enabled)
    save_mcp_state(state, home)


def apply_state_overrides(
    servers: dict[str, dict[str, Any]], home: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Apply machine-local server state overrides onto a manifest servers dict."""
    state = load_mcp_state(home)
    local_servers = state.get("servers")
    if not isinstance(local_servers, dict) or not local_servers:
        return dict(servers)
    merged: dict[str, dict[str, Any]] = dict(servers)
    for name, overrides in local_servers.items():
        if name in merged and isinstance(overrides, dict):
            merged[name] = {**merged[name], **overrides}
    return merged
