"""Claude Desktop MCP dialect (`mcpServers` in `claude_desktop_config.json`).

The Electron app, not the CLI: it owns everything else in this file
(preferences, window state), so the renderer only touches the `mcpServers`
table, additively, and never while the app is running (it rewrites the
file on save; the next guard cycle heals the race).

Default deny: a server is rendered here only when its manifest entry sets
`desktop_allow: true`. The Desktop app has no guardrail hooks, so it is
the least protected runtime; handing it the full CLI toolset (Vault
writes, workflow mutation, arbitrary browser code) would leave only the
"Allow" popup between an untrusted document and the host. Manifest-known
but not allowed servers found live are never removed (additive rule);
`check_desktop_allowlist` reports them instead.
"""
from __future__ import annotations

import copy
import logging

from nexgen_core.i18n import t
from nexgen_core.mcp_render import load_json_object, write_json_config
from nexgen_core.paths import claude_desktop_config

logger = logging.getLogger("nexgen.render.desktop")

#: The manifest flag that opts a server into the Desktop app. Absent or
#: not exactly True means deny, whatever the server's `targets` say.
DESKTOP_ALLOW_KEY = "desktop_allow"


def allowed_desktop_servers(renderer) -> dict[str, dict]:
    """Resolved manifest entries explicitly allowed on the Desktop app.

    Resolution is the shared one (`McpRenderer._resolve_entry`), so what
    Desktop launches is expanded exactly like what a CLI would launch for
    the same entry. Placement (`targets`, `exposure`) does not apply here:
    the allowlist flag is the only gate, which is why it defaults to deny.
    Only stdio entries are emitted: no allowlisted server uses HTTP today,
    and the Desktop HTTP shape stays unverified until one does.
    """
    from nexgen_core.config import load_mcp_manifest

    raw: dict = {}
    if renderer.manifest_path.is_file():
        raw = load_mcp_manifest(renderer.manifest_path).get("servers", {})
    allowed: dict[str, dict] = {}
    for name, srv in raw.items():
        if not isinstance(srv, dict) or srv.get("enabled") is False:
            continue
        if srv.get(DESKTOP_ALLOW_KEY) is not True:
            continue
        entry = renderer._resolve_entry(name, srv, "claude-desktop")
        if entry is None:
            continue
        if entry.get("transport") == "http" or entry.get("url"):
            logger.warning("MCP server %r is allowed on Desktop but uses HTTP: skipped", name)
            continue
        allowed[name] = entry
    return allowed


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates the Desktop app's `mcpServers` table, nothing else."""
    servers = allowed_desktop_servers(renderer)
    cfg_file = claude_desktop_config(renderer.home)
    existing = load_json_object(cfg_file)
    before = copy.deepcopy(existing)

    mcp_servers = existing.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise ValueError(f"Could not render {cfg_file}: mcpServers must be an object")
    for retired in renderer.retired_server_names():
        mcp_servers.pop(retired, None)
    for name, srv in servers.items():
        mcp_servers[name] = {
            "command": srv.get("command", ""),
            "args": srv.get("args", []),
            "env": srv.get("env", {}),
        }

    if mcp_servers or "mcpServers" in existing:
        existing["mcpServers"] = mcp_servers
    if not cfg_file.is_file() and "mcpServers" not in existing:
        # Never launched and nothing to say: unlike the CLIs, the app also
        # owns first-launch defaults in this file, so a bare managed file
        # would only get in their way.
        return True, t("Claude Desktop configuration updated")
    if write:
        write_json_config(renderer, cfg_file, before, existing)
    return True, t("Claude Desktop configuration updated")
