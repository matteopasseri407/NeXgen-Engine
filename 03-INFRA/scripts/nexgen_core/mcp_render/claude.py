"""Claude Code MCP dialect (`mcpServers` in `~/.claude.json`)."""
from __future__ import annotations

import copy

from nexgen_core.i18n import t
from nexgen_core.mcp_render import load_json_object, write_json_config
from nexgen_core.paths import claude_config


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates the MCP configuration for Claude Code (~/.claude.json)."""
    servers = renderer.load_resolved_servers("claude")
    cfg_file = claude_config(renderer.home)
    existing = load_json_object(cfg_file)
    before = copy.deepcopy(existing)

    mcp_servers = existing.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise ValueError(f"Could not render {cfg_file}: mcpServers must be an object")
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
        write_json_config(renderer, cfg_file, before, existing)
    return True, t("Claude configuration updated")
