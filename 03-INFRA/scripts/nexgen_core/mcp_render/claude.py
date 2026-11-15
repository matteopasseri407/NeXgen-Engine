"""Claude Code MCP dialect (`mcpServers` in `~/.claude.json`)."""
from __future__ import annotations

import copy

from nexgen_core.i18n import t
from nexgen_core.mcp_render import load_json_object, merge_entry, write_json_config
from nexgen_core.paths import claude_config

#: The per-server keys this renderer writes. Anything else in an entry is Claude's
#: own state (the `disabled` toggle, a tool list the app screen set) and is kept.
_OWNED_KEYS = frozenset({"type", "command", "args", "env", "url", "headers"})


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
            entry = {
                "type": "http",
                "url": srv["url"],
                "headers": headers,
            }
        else:
            entry = {
                "type": "stdio",
                "command": srv.get("command", ""),
                "args": srv.get("args", []),
                "env": srv.get("env", {}),
            }
        mcp_servers[name] = merge_entry(mcp_servers.get(name), entry, _OWNED_KEYS)

    existing["mcpServers"] = mcp_servers
    if write:
        if cfg_file.is_file() and not renderer.previewing:
            # Claude Code and the Desktop app save session and trust state into
            # this same file, and one of those saves can land between the read
            # above and this write: rebase on the file as it is now and re-apply
            # only `mcpServers`, the table this render owns, so nothing the CLI
            # wrote in between is lost. The re-read sits after the table is
            # computed on purpose: any earlier and the save it protects against
            # still has the whole write to land in.
            fresh = load_json_object(cfg_file)
            freshly_read = copy.deepcopy(fresh)
            fresh["mcpServers"] = mcp_servers
            write_json_config(renderer, cfg_file, freshly_read, fresh)
        else:
            write_json_config(renderer, cfg_file, before, existing)
    return True, t("Claude configuration updated")
