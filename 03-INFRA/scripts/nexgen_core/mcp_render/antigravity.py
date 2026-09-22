"""Antigravity MCP dialect (canonical file + fan-out symlinks)."""
from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.mcp_render import IS_WINDOWS, MCP_REMOTE_PACKAGE
from nexgen_core.paths import ANTIGRAVITY_CONSUMER_DIRS, antigravity_config


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates Antigravity's MCP configuration and fans it out to its consumers."""
    servers = renderer.load_resolved_servers("antigravity")
    cfg_file = antigravity_config(renderer.home)
    existing: dict[str, Any] = {}
    if cfg_file.is_file():
        try:
            existing = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Could not parse {cfg_file}: invalid JSON ({exc})")

    mcp_servers = existing.get("mcpServers", {})
    for retired in renderer.retired_server_names():
        mcp_servers.pop(retired, None)
    renderer._drop_unmounted(mcp_servers, servers, cli_target="antigravity")
    bridge_script = renderer.engine_root / "agent-universal-layer" / "mcp" / "mcp-http-bridge.mjs"

    for name, srv in servers.items():
        if srv.get("transport") == "http" or srv.get("url"):
            auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else ""
            node_cmd = "node.exe" if IS_WINDOWS else "node"
            mcp_servers[name] = {
                "command": node_cmd,
                "args": [str(bridge_script), srv["url"], auth_env, MCP_REMOTE_PACKAGE],
                "env": srv.get("env", {}),
            }
        else:
            mcp_servers[name] = {
                "command": srv.get("command", ""),
                "args": srv.get("args", []),
                "env": srv.get("env", {}),
            }

    existing["mcpServers"] = mcp_servers
    if write:
        renderer._backup_and_write(cfg_file, json.dumps(existing, indent=2) + "\n")
        _fan_out_antigravity(renderer, cfg_file)
    return True, t("Antigravity configuration updated")

def _fan_out_antigravity(renderer, canonical: Path) -> None:
    """Points every path Antigravity reads from at the canonical file.

    Where links can't be created (Windows without privileges) it copies
    instead: what matters is that no variant is left behind.
    """
    for directory in ANTIGRAVITY_CONSUMER_DIRS:
        target = renderer.home / ".gemini" / directory / "mcp_config.json"
        if target == canonical:
            continue
        try:
            if target.is_symlink() and target.resolve() == canonical.resolve():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(canonical)
        except OSError:
            with contextlib.suppress(OSError):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(canonical, target)
