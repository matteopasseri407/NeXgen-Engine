"""Codex MCP dialect (TOML `~/.codex/config.toml`)."""
from __future__ import annotations

import json

from nexgen_core.config import load_mcp_manifest
from nexgen_core.i18n import t
from nexgen_core.paths import codex_config


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates Codex's native MCP configuration (~/.codex/config.toml).

    Additive like the other three CLIs: an existing server that is not in
    the manifest is preserved verbatim, and one that is env-gated but not
    resolvable right now (e.g. the recurring guard running without the
    user's shell environment) keeps the entry already on disk instead of
    deleting it. Only `retired_servers` remove entries; manifest servers
    are always re-emitted fresh.
    """
    servers = renderer.load_resolved_servers("codex")
    cfg_file = codex_config(renderer.home)

    retired = renderer.retired_server_names()
    _manifest_data = load_mcp_manifest(renderer.manifest_path) if renderer.manifest_path.is_file() else {}
    unmounted = {
        name.replace("-", "_")
        for name, srv in _manifest_data.get("servers", {}).items()
        if name not in servers and name not in retired
        and not (
            srv.get("require_env")
            and (str(srv.get("tier", "")).strip().lower() == "core" or srv.get("enabled", False))
            and not (srv.get("lazy") and "codex" in (srv.get("lazy_targets") or ["claude", "codex", "antigravity", "opencode"]))
        )
    }
    managed = {name.replace("-", "_") for name in servers} | {name.replace("-", "_") for name in retired}

    existing_lines: list[str] = []
    preserved_lines: list[str] = []
    if cfg_file.is_file():
        try:
            raw = cfg_file.read_text(encoding="utf-8")
            # Preserves existing non-MCP sections (e.g. [model], general
            # settings) and the mcp_servers entries this engine doesn't own.
            in_mcp_section = False
            keep_current = False
            for line in raw.splitlines():
                stripped = line.strip()
                if stripped.startswith("[mcp_servers."):
                    in_mcp_section = True
                    key = stripped[13:-1] if stripped.endswith("]") else stripped[13:]
                    section = key.split(".", 1)[0]
                    keep_current = section not in managed and section not in unmounted
                    if keep_current:
                        preserved_lines.append(line)
                    continue
                elif stripped.startswith("[") and not stripped.startswith("[mcp_servers."):
                    in_mcp_section = False
                    keep_current = False
                if not in_mcp_section and not line.startswith("# NeXgen Engine"):
                    existing_lines.append(line)
                elif in_mcp_section and keep_current:
                    preserved_lines.append(line)
        except OSError:
            existing_lines = []
            preserved_lines = []

    header = "# NeXgen Engine - Codex MCP configuration, auto-generated"
    lines: list[str] = []
    if existing_lines:
        cleaned_existing = "\n".join(existing_lines).strip()
        if cleaned_existing:
            lines.append(cleaned_existing)
            lines.append("")

    lines.append(header)
    lines.append("")
    if preserved_lines:
        lines.append("\n".join(preserved_lines).strip())
        lines.append("")
    for name, srv in servers.items():
        safe_name = name.replace("-", "_")
        lines.append(f"[mcp_servers.{safe_name}]")
        if srv.get("transport") == "http" or srv.get("url"):
            lines.append(f'url = {json.dumps(srv["url"])}')
            auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else None
            if auth_env:
                lines.append(f'bearer_token_env_var = "{auth_env}"')
            timeouts = srv.get("timeouts", {})
            if isinstance(timeouts, dict):
                if "startup" in timeouts:
                    lines.append(f'startup_timeout_sec = {float(timeouts["startup"])}')
                if "tool" in timeouts:
                    lines.append(f'tool_timeout_sec = {float(timeouts["tool"])}')
        else:
            lines.append(f'command = {json.dumps(srv.get("command", ""))}')
            args_json = json.dumps(srv.get("args", []))
            lines.append(f"args = {args_json}")
            env = srv.get("env", {})
            if env:
                lines.append(f"[mcp_servers.{safe_name}.env]")
                for k, v in env.items():
                    lines.append(f'{k} = {json.dumps(str(v))}')
        lines.append("")

    content = "\n".join(lines).strip() + "\n"
    if write:
        renderer._backup_and_write(cfg_file, content)
    return True, t("Codex configuration updated")
