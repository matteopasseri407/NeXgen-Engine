"""Antigravity MCP dialect (canonical file + fan-out symlinks)."""
from __future__ import annotations

import contextlib
import copy
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text, backup_file, publish_symlink
from nexgen_core.i18n import t
from nexgen_core.mcp_render import IS_WINDOWS, MCP_REMOTE_PACKAGE, load_json_object, write_json_config
from nexgen_core.paths import ANTIGRAVITY_CONSUMER_DIRS, antigravity_config


def _consumer_paths(renderer, canonical: Path) -> list[Path]:
    """Every path besides the canonical file that Antigravity reads its servers from."""
    paths = [renderer.home / ".gemini" / directory / "mcp_config.json" for directory in ANTIGRAVITY_CONSUMER_DIRS]
    return [path for path in paths if path != canonical]


def _servers_kept_in_copies(renderer, canonical: Path) -> dict[str, Any]:
    """Servers that live only in a real file at one of the consumer paths.

    Those paths used to be deleted and replaced by a link to the canonical
    file with no backup, so whatever someone had added to one of them (a
    server added from Antigravity's own screen, or by hand) disappeared at
    the next guard cycle. They are carried into the canonical file instead,
    where the rest of the engine already treats unknown servers as the
    user's. A copy that cannot be read contributes nothing, but it is still
    backed up before it is replaced.
    """
    kept: dict[str, Any] = {}
    for path in _consumer_paths(renderer, canonical):
        if path.is_symlink() or not path.is_file():
            continue
        with contextlib.suppress(ValueError, OSError):
            servers = load_json_object(path).get("mcpServers")
            if isinstance(servers, dict):
                for name, entry in servers.items():
                    kept.setdefault(name, entry)
    return kept


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates Antigravity's MCP configuration and fans it out to its consumers."""
    servers = renderer.load_resolved_servers("antigravity")
    cfg_file = antigravity_config(renderer.home)
    existing = load_json_object(cfg_file)
    before = copy.deepcopy(existing)

    mcp_servers = existing.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise ValueError(f"Could not render {cfg_file}: mcpServers must be an object")
    for name, entry in _servers_kept_in_copies(renderer, cfg_file).items():
        mcp_servers.setdefault(name, entry)
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
        write_json_config(renderer, cfg_file, before, existing)
        if not renderer.previewing:
            _fan_out_antigravity(renderer, cfg_file)
    return True, t("Antigravity configuration updated")


def _fan_out_antigravity(renderer, canonical: Path) -> None:
    """Points every path Antigravity reads from at the canonical file.

    Where links can't be created (Windows without privileges) it copies
    instead: what matters is that no variant is left behind. A real file
    that already holds the canonical bytes is left alone (the copy from the
    last cycle: replacing it again every half hour was churn), and one that
    differs is backed up before it is replaced, never just deleted.
    """
    for target in _consumer_paths(renderer, canonical):
        try:
            if target.is_symlink() and target.resolve() == canonical.resolve():
                continue
            if not target.is_symlink() and target.is_file() and target.read_bytes() == canonical.read_bytes():
                continue
            if not target.is_symlink() and target.is_file():
                backup_file(target, keep=3)
            publish_symlink(target, canonical)
        except OSError:
            with contextlib.suppress(OSError):
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(target, canonical.read_text(encoding="utf-8"))
