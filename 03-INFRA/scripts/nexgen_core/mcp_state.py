"""Machine-local state and runtime overrides for MCP servers.

Operational policy: manifest.yaml is tracked in the engine repository or Vault
and must never be modified at runtime. Local enablement or machine-specific
server state is kept in the machine's local state directory (mcp-state.json).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexgen_core.files import write_private_text
from nexgen_core.paths import resolve_state_dir

FILENAME = "mcp-state.json"


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
