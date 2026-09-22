"""Claude Code MCP dialect (`mcpServers` in `~/.claude.json`)."""
from __future__ import annotations

import json
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.paths import claude_config


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates the MCP configuration for Claude Code (~/.claude.json)."""
    servers = renderer.load_resolved_servers("claude")
    cfg_file = claude_config(renderer.home)
    existing: dict[str, Any] = {}
    if cfg_file.is_file():
        try:
            existing = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Could not parse {cfg_file}: invalid JSON ({exc})")

    mcp_servers = existing.get("mcpServers", {})
    for retired in renderer.retired_server_names():
        mcp_servers.pop(retired, None)
    renderer._drop_unmounted(mcp_servers, servers, cli_target="claude")
    for name, srv in servers.items():
        if srv.get("transport") == "http" or srv.get("url"):
            auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else None
            headers = {"Authorization": f"Bearer ${{{auth_env}}}"} if auth_env else {}
            mcp_servers[name] = {
                "type": "http",
                "url": srv["url"],
                "headers": headers,
            }
        else:
            mcp_servers[name] = {
                "type": "stdio",
                "command": srv.get("command", ""),
                "args": srv.get("args", []),
                "env": srv.get("env", {}),
            }

    existing["mcpServers"] = mcp_servers
    if write:
        renderer._backup_and_write(cfg_file, json.dumps(existing, indent=2) + "\n")
    return True, t("Claude configuration updated")
