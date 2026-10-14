"""Claude Desktop MCP dialect (`mcpServers` in `claude_desktop_config.json`).

The Electron app, not the CLI: it owns everything else in this file
(preferences, window state), so the renderer only touches the `mcpServers`
table, additively, and re-reads the file just before writing (the app may
save preferences at any moment; a blind overwrite would lose them, and a
write the app later supersedes heals on the next guard cycle).

Default deny: a server is rendered here only when its manifest entry sets
`desktop_allow: true`. The Desktop app has no guardrail hooks, so it is
the least protected runtime; handing it the full CLI toolset (Vault
writes, workflow mutation, arbitrary browser code) would leave only the
"Allow" popup between an untrusted document and the host. Revocation is
tracked in a sidecar file: names the renderer wrote that lose the flag
are removed again, while entries the renderer never wrote stay additive
and `check_desktop_allowlist` reports the manifest-known ones instead of
removing them.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.mcp_placement import GATEWAY
from nexgen_core.mcp_render import load_json_object, write_json_config
from nexgen_core.paths import claude_desktop_config

logger = logging.getLogger("nexgen.render.desktop")

#: The manifest flag that opts a server into the Desktop app. Absent or
#: not exactly True means deny, whatever the server's `targets` say.
DESKTOP_ALLOW_KEY = "desktop_allow"


def _managed_path(cfg_file: Path) -> Path:
    """Sidecar tracking the server names this renderer wrote, so revoking
    the flag removes exactly those and never a hand-added entry."""
    return cfg_file.with_suffix(".managed.json")


def _read_managed(cfg_file: Path) -> set[str]:
    try:
        data = json.loads(_managed_path(cfg_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    names = data.get("mcpServers") if isinstance(data, dict) else None
    return set(names) if isinstance(names, list) and all(isinstance(n, str) for n in names) else set()


def allowed_desktop_servers(renderer: Any) -> dict[str, dict]:
    """Resolved manifest entries explicitly allowed on the Desktop app.

    Resolution is the shared one (`McpRenderer._resolve_entry`), so what
    Desktop launches is expanded exactly like what a CLI would launch for
    the same entry. Placement (`targets`, `exposure`) does not apply here:
    the allowlist flag is the only gate, which is why it defaults to deny.
    Only stdio entries are emitted: no allowlisted server uses HTTP today,
    and the Desktop HTTP shape stays unverified until one does. The
    gateway itself is refused even when flagged: behind it sit every lazy
    server, mutating ones included.
    """
    from nexgen_core.config import load_mcp_manifest

    raw: dict = {}
    if renderer.manifest_path.is_file():
        raw = load_mcp_manifest(renderer.manifest_path).get("servers", {}) or {}
    allowed: dict[str, dict] = {}
    for name, srv in raw.items():
        if not isinstance(srv, dict) or srv.get("enabled") is False:
            continue
        if srv.get(DESKTOP_ALLOW_KEY) is not True:
            continue
        if name == GATEWAY:
            logger.error(
                "MCP server %r carries desktop_allow: the gateway must never mount on Desktop; skipped", name
            )
            continue
        entry = renderer._resolve_entry(name, srv, "claude-desktop", raw.get(GATEWAY))
        if entry is None:
            continue
        if entry.get("transport") == "http" or entry.get("url"):
            logger.warning("MCP server %r is allowed on Desktop but uses HTTP: skipped", name)
            continue
        allowed[name] = entry
    return allowed


def gated_desktop_names(renderer) -> set[str]:
    """Allowlisted servers whose `require_env` is unset in this process.

    The recurring guard runs without the shell environment: these resolve
    from an interactive apply and not from the guard, so the guard must
    leave their entries alone instead of deleting what the shell wrote.
    """
    from nexgen_core.config import load_mcp_manifest

    if not renderer.manifest_path.is_file():
        return set()
    raw = load_mcp_manifest(renderer.manifest_path).get("servers", {}) or {}
    return {
        name for name, srv in raw.items()
        if isinstance(srv, dict) and srv.get("enabled") is not False
        and srv.get(DESKTOP_ALLOW_KEY) is True and name != GATEWAY
        # Desktop only mounts stdio, so an HTTP entry is never pending.
        and srv.get("transport") != "http" and not srv.get("url")
        and srv.get("require_env") and not os.environ.get(srv["require_env"])
    } - set(renderer.retired_server_names())


def _names_command(path: str, command: str, windows: bool | None = None) -> bool:
    """True when `path` is an executable called `command`.

    Windows matches case-insensitively and through PATHEXT, which is how
    `shutil.which` found it (it answers `tool.EXE` for `tool`).
    """
    name = Path(path).name
    if not (os.name == "nt" if windows is None else windows):
        return name == command
    exts = [e.lower() for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT;.COM").split(";") if e]
    stem, suffix = os.path.splitext(name.lower())
    return name.lower() == command.lower() or (stem == command.lower() and suffix in exts)


def _absolute_command(command: str, previous: str = "") -> str:
    """Bare commands depend on the launcher's PATH, and the app starts
    from the graphical session with a minimal one (macOS especially).
    Resolve what the renderer can see; keep the bare name (with a
    warning) when it cannot, instead of writing an entry known dead.

    A path written earlier for the same command wins while it still
    exists: the guard and an interactive shell see different PATHs, and
    re-resolving each time made them rewrite the entry against each other.
    """
    if not command or "/" in command or "\\" in command:
        return command
    if previous and _names_command(previous, command) and os.access(previous, os.X_OK):
        return previous
    found = shutil.which(command)
    return found or command


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates the Desktop app's `mcpServers` table, nothing else."""
    servers = allowed_desktop_servers(renderer)
    cfg_file = claude_desktop_config(renderer.home)
    existing = load_json_object(cfg_file)
    before = copy.deepcopy(existing)

    if not cfg_file.is_file() and not cfg_file.parent.is_dir():
        # No app directory: no evidence the app ever launched here (a
        # headless machine, for example). Creating the file would only
        # get in the way of first-launch defaults.
        return True, t("Claude Desktop configuration updated")

    if write and not renderer.previewing and cfg_file.is_file():
        # Rebase onto what the app may have saved while resolving: user
        # additions survive, managed entries are re-applied below.
        existing = load_json_object(cfg_file)

    mcp_servers = existing.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise ValueError(f"Could not render {cfg_file}: mcpServers must be an object")
    for retired in renderer.retired_server_names():
        mcp_servers.pop(retired, None)
    gated = gated_desktop_names(renderer) & set(mcp_servers)
    for stale in _read_managed(cfg_file) - set(servers) - gated:
        mcp_servers.pop(stale, None)
    for name, srv in servers.items():
        raw_command = srv.get("command", "")
        previous = mcp_servers.get(name, {}).get("command", "") if isinstance(mcp_servers.get(name), dict) else ""
        command = _absolute_command(raw_command, previous)
        if command == raw_command and raw_command and "/" not in raw_command and "\\" not in raw_command:
            logger.warning("MCP server %r: command %r not on PATH, writing it bare for Desktop", name, raw_command)
        mcp_servers[name] = {
            "command": command,
            "args": srv.get("args", []),
            "env": srv.get("env", {}),
        }

    if mcp_servers or "mcpServers" in existing:
        existing["mcpServers"] = mcp_servers
    if not cfg_file.is_file() and "mcpServers" not in existing:
        # Never launched and nothing to say: the app also owns
        # first-launch defaults in this file, so a bare managed file
        # would only get in their way.
        return True, t("Claude Desktop configuration updated")
    sidecar = _managed_path(cfg_file)
    try:
        sidecar_before = sidecar.read_text(encoding="utf-8")
    except OSError:
        sidecar_before = None
    sidecar_text = json.dumps({"mcpServers": sorted(set(servers) | gated)}, indent=2) + "\n"
    if write:
        write_json_config(renderer, cfg_file, before, existing)
        if sidecar_text != sidecar_before:
            renderer._backup_and_write(sidecar, sidecar_text)
    return True, t("Claude Desktop configuration updated")
