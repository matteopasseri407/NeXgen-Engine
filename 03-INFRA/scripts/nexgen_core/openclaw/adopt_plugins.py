"""What installed OpenClaw plugins wrap an MCP server, and how to adopt them.

One job: inspect installed OpenClaw plugins, distinguish MCP wrappers (ClawHub
plugins wrapping a remote or local MCP server) from other plugins (channels,
providers, memory, etc.), and extract their underlying MCP server definitions
so other harnesses receive them from the canonical library.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.adopt_scan import UnmanagedServer

#: Pattern matching descriptions that announce an MCP service wrapper.
_MCP_WRAPPER_DESC = re.compile(
    r"\b(?:via\s+.*?\b(?:official\s+mcp(?:\s+service)?|hosted\s+mcp)|hosted\s+mcp|official\s+mcp)\b",
    re.IGNORECASE,
)


def _index_catalog_entry(catalog: dict[str, dict[str, Any]], entry: Mapping[str, Any]) -> None:
    """Index catalog entry by plugin id and package name."""
    openclaw_meta = entry.get("openclaw") if isinstance(entry.get("openclaw"), Mapping) else {}
    plugin_meta = openclaw_meta.get("plugin") if isinstance(openclaw_meta.get("plugin"), Mapping) else {}
    pid = plugin_meta.get("id")
    if pid:
        catalog[str(pid)] = dict(entry)
    name = entry.get("name")
    if name:
        catalog[str(name)] = dict(entry)


def read_catalog_snapshots(state_dir: Path) -> dict[str, dict[str, Any]]:
    """Read official_external_plugin_catalog_snapshots from openclaw.sqlite in read-only mode."""
    db_path = state_dir / "state" / "openclaw.sqlite"
    if not db_path.is_file():
        db_path = state_dir / "openclaw.sqlite"
    if not db_path.is_file():
        return {}
    catalog: dict[str, dict[str, Any]] = {}
    try:
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT body FROM official_external_plugin_catalog_snapshots")
            for (body_text,) in cursor.fetchall():
                if not isinstance(body_text, str):
                    continue
                try:
                    payload = json.loads(body_text)
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):  # nexgen: allow reason="corrupted catalog entry row is skipped"
                    continue
                entries = payload.get("entries") if isinstance(payload, Mapping) else None
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, Mapping):
                            _index_catalog_entry(catalog, entry)
    except (sqlite3.Error, OSError):  # nexgen: allow reason="unreadable, locked, or absent sqlite DB returns empty catalog"
        return {}
    return catalog


def _read_json(path: Path) -> dict[str, Any] | None:
    """Tolerantly read a JSON file; None on error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):  # nexgen: allow reason="unreadable or invalid json file returns None"
        return None


def _find_installed_plugin_ids(state_dir: Path, config: Mapping[str, Any] | None) -> list[str]:
    """All installed plugin IDs from config entries and the extensions folder."""
    ids: set[str] = set()
    plugins = config.get("plugins") if isinstance(config, Mapping) else None
    entries = plugins.get("entries") if isinstance(plugins, Mapping) else None
    if isinstance(entries, Mapping):
        ids.update(str(k) for k in entries)
    ext_dir = state_dir / "extensions"
    if ext_dir.is_dir():
        try:
            for child in ext_dir.iterdir():
                if child.is_dir() and not child.name.startswith((".", "_")):
                    ids.add(child.name)
        except OSError:  # nexgen: allow reason="unreadable extensions folder returns accumulated IDs"
            pass
    return sorted(ids)


def _extract_manifests(ext_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Read openclaw.plugin.json, package.json, and mcp.json if present."""
    plugin_json = _read_json(ext_dir / "openclaw.plugin.json") or {}
    pkg_json = _read_json(ext_dir / "package.json") or {}
    mcp_json = _read_json(ext_dir / "mcp.json") or _read_json(ext_dir / ".mcp.json") or {}
    return plugin_json, pkg_json, mcp_json


def _extract_mcp_servers_from_manifests(
    plugin_json: Mapping[str, Any],
    pkg_json: Mapping[str, Any],
    mcp_json: Mapping[str, Any],
    cat_entry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Find server definitions declared in plugin manifests or catalog entry."""
    servers = plugin_json.get("mcpServers") or plugin_json.get("mcp")
    if isinstance(servers, Mapping) and servers:
        return dict(servers)
    servers = mcp_json.get("mcpServers") or mcp_json.get("servers")
    if isinstance(servers, Mapping) and servers:
        return dict(servers)
    oc_pkg = pkg_json.get("openclaw") if isinstance(pkg_json.get("openclaw"), Mapping) else {}
    servers = oc_pkg.get("mcpServers") or oc_pkg.get("mcp")
    if isinstance(servers, Mapping) and servers:
        return dict(servers)
    if cat_entry:
        oc_cat = cat_entry.get("openclaw") if isinstance(cat_entry.get("openclaw"), Mapping) else {}
        servers = oc_cat.get("mcpServers") or oc_cat.get("mcp") or cat_entry.get("mcpServers")
        if isinstance(servers, Mapping) and servers:
            return dict(servers)
    return {}


def _is_mcp_wrapper_description(
    plugin_json: Mapping[str, Any],
    pkg_json: Mapping[str, Any],
    cat_entry: Mapping[str, Any] | None,
) -> bool:
    """Whether any description mentions being an MCP wrapper."""
    for desc in (
        plugin_json.get("description"),
        pkg_json.get("description"),
        cat_entry.get("description") if cat_entry else None,
    ):
        if isinstance(desc, str) and _MCP_WRAPPER_DESC.search(desc):
            return True
    return False


def _build_http_spec(
    srv_def: Mapping[str, Any],
    plugin_cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Build spec for an HTTP/remote MCP server, preserving auth tokens."""
    url = srv_def.get("url") or plugin_cfg.get("url") or plugin_cfg.get("serverUrl") or plugin_cfg.get("endpoint")
    if url is None:
        return None
    spec: dict[str, Any] = {
        "transport": "http",
        "url": str(url),
    }
    headers = dict(srv_def.get("headers") or {})
    if isinstance(plugin_cfg.get("headers"), Mapping):
        headers.update(dict(plugin_cfg["headers"]))
    auth = srv_def.get("auth") or plugin_cfg.get("auth")
    if isinstance(auth, Mapping):
        spec["auth"] = dict(auth)
        env_var = auth.get("env")
        if env_var and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer ${{{env_var}}}"
    else:
        token = plugin_cfg.get("apiKey") or plugin_cfg.get("token")
        if isinstance(token, str):
            if token.startswith("${") and token.endswith("}"):
                spec["auth"] = {"type": "bearer", "env": token[2:-1]}
                if "Authorization" not in headers:
                    headers["Authorization"] = f"Bearer {token}"
            else:
                headers["Authorization"] = f"Bearer {token}"
    if headers:
        spec["headers"] = headers
    return spec


def _build_stdio_spec(
    srv_def: Mapping[str, Any],
    plugin_cfg: Mapping[str, Any],
    ext_dir: Path,
) -> dict[str, Any]:
    """Build spec for a local stdio MCP server, resolving bundle paths."""
    command = srv_def.get("command") or plugin_cfg.get("command")
    if command is None:
        return {}
    cmd_str = str(command)
    ext_str = ext_dir.resolve().as_posix()
    cmd_str = cmd_str.replace("${PLUGIN_ROOT}", ext_str).replace("${CLAUDE_PLUGIN_ROOT}", ext_str)
    if cmd_str.startswith("./") and ext_dir.is_dir():
        cmd_str = (ext_dir / cmd_str).resolve().as_posix()

    args = srv_def.get("args") or plugin_cfg.get("args")
    raw_args = list(args) if isinstance(args, list) else []
    resolved_args: list[str] = []
    for a in raw_args:
        s = str(a).replace("${PLUGIN_ROOT}", ext_str).replace("${CLAUDE_PLUGIN_ROOT}", ext_str)
        if s.startswith("./") and ext_dir.is_dir():
            s = (ext_dir / s).resolve().as_posix()
        resolved_args.append(s)

    env = dict(srv_def.get("env") or {})
    if isinstance(plugin_cfg.get("env"), Mapping):
        env.update(dict(plugin_cfg["env"]))

    spec: dict[str, Any] = {
        "transport": "stdio",
        "command": cmd_str,
    }
    if resolved_args:
        spec["args"] = resolved_args
    if env:
        spec["env"] = env
    return spec


def _build_server_spec(
    _server_name: str,
    srv_def: Mapping[str, Any],
    plugin_cfg: Mapping[str, Any],
    ext_dir: Path,
) -> dict[str, Any]:
    """Build the UnmanagedServer spec dictionary from manifest and plugin config."""
    http_spec = _build_http_spec(srv_def, plugin_cfg)
    if http_spec is not None:
        return http_spec
    return _build_stdio_spec(srv_def, plugin_cfg, ext_dir)


def _plugin_kind(
    plugin_json: Mapping[str, Any],
    pkg_json: Mapping[str, Any],
    cat_entry: Mapping[str, Any] | None,
) -> str:
    """The kind of a plugin (channel, provider, memory, etc.)."""
    oc_pkg = pkg_json.get("openclaw") if isinstance(pkg_json.get("openclaw"), Mapping) else {}
    for candidate in (
        plugin_json.get("kind"),
        oc_pkg.get("kind"),
        cat_entry.get("kind") if cat_entry else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "extension"


def scan_plugins(
    state_dir: Path,
    config: Mapping[str, Any] | None,
    declared: set[str],
) -> tuple[list[UnmanagedServer], list[str]]:
    """Scan installed OpenClaw plugins: extract MCP wrappers, report non-wrappers."""
    plugin_ids = _find_installed_plugin_ids(state_dir, config)
    catalog = read_catalog_snapshots(state_dir)
    plugins_cfg = config.get("plugins") if isinstance(config, Mapping) else None
    entries = plugins_cfg.get("entries") if isinstance(plugins_cfg, Mapping) else {}

    servers: list[UnmanagedServer] = []
    notes: list[str] = []

    for pid in plugin_ids:
        ext_dir = state_dir / "extensions" / pid
        plugin_json, pkg_json, mcp_json = _extract_manifests(ext_dir)
        plugin_entry = entries.get(pid) if isinstance(entries, Mapping) else None
        plugin_cfg = plugin_entry.get("config") if isinstance(plugin_entry, Mapping) and isinstance(plugin_entry.get("config"), Mapping) else {}
        cat_entry = catalog.get(pid) or catalog.get(f"@openclaw/{pid}")

        declared_servers = _extract_mcp_servers_from_manifests(plugin_json, pkg_json, mcp_json, cat_entry)
        is_wrapper_desc = _is_mcp_wrapper_description(plugin_json, pkg_json, cat_entry)
        has_cfg_server = bool(
            plugin_cfg.get("url") or plugin_cfg.get("serverUrl") or plugin_cfg.get("endpoint") or plugin_cfg.get("command")
        )

        kind = _plugin_kind(plugin_json, pkg_json, cat_entry)

        if declared_servers:
            for srv_name, srv_def in declared_servers.items():
                name = pid if srv_name in ("default", "") else srv_name
                if name in declared:
                    continue
                spec = _build_server_spec(name, srv_def if isinstance(srv_def, Mapping) else {}, plugin_cfg, ext_dir)
                if spec:
                    servers.append(UnmanagedServer(name, spec))
        elif is_wrapper_desc and has_cfg_server:
            name = pid
            if name not in declared:
                spec = _build_server_spec(name, {}, plugin_cfg, ext_dir)
                if spec:
                    servers.append(UnmanagedServer(name, spec))
        else:
            notes.append(t("plugin '{id}' is not an MCP wrapper ({kind}): not adoptable", id=pid, kind=kind))

    return servers, notes
