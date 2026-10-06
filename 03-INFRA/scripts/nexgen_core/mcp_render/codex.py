"""Codex MCP dialect (TOML `~/.codex/config.toml`)."""
from __future__ import annotations

import json
import re
import tomllib

from nexgen_core.i18n import t
from nexgen_core.paths import codex_config


def _toml_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name)


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
    unmounted = {
        name.replace("-", "_")
        for name in renderer.unmounted_server_names(servers, "codex")
    }
    managed = {name.replace("-", "_") for name in servers} | {name.replace("-", "_") for name in retired}

    existing_lines: list[str] = []
    preserved_lines: list[str] = []
    previous = {}
    if cfg_file.is_file():
        try:
            raw = cfg_file.read_text(encoding="utf-8")
            previous = tomllib.loads(raw)
            # Preserves existing non-MCP sections (e.g. [model], general
            # settings) and the mcp_servers entries this engine doesn't own.
            in_mcp_section = False
            keep_current = False
            for line in raw.splitlines():
                stripped = line.strip()
                if stripped.startswith("[mcp_servers."):
                    in_mcp_section = True
                    section = next(iter(tomllib.loads(stripped)["mcp_servers"]))
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
        except (OSError, ValueError) as exc:
            raise ValueError("Cannot read or parse the Codex configuration; original preserved.") from exc

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
        section = _toml_key(safe_name)
        lines.append(f"[mcp_servers.{section}]")
        if srv.get("transport") == "http" or srv.get("url"):
            lines.append(f'url = {json.dumps(srv["url"])}')
            auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else None
            if auth_env:
                lines.append(f'bearer_token_env_var = "{auth_env}"')
        else:
            lines.append(f'command = {json.dumps(srv.get("command", ""))}')
            args_json = json.dumps(srv.get("args", []))
            lines.append(f"args = {args_json}")
        # The timeouts the manifest declares apply to both kinds of server. Only the http branch
        # wrote them, so a stdio server (vault-ocr, drive and lane: 30 s to start, up to 300 s per tool)
        # ran on Codex's defaults (10 s to start), and the manifest comment claiming "Codex renders its native
        # timeout fields" was true for half the servers. They go before the env sub-table: a key
        # after a `[table]` header belongs to that table.
        timeouts = srv.get("timeouts", {})
        if isinstance(timeouts, dict):
            if "startup" in timeouts:
                lines.append(f'startup_timeout_sec = {float(timeouts["startup"])}')
            if "tool" in timeouts:
                lines.append(f'tool_timeout_sec = {float(timeouts["tool"])}')
        if not (srv.get("transport") == "http" or srv.get("url")):
            env = srv.get("env", {})
            if env:
                lines.append(f"[mcp_servers.{section}.env]")
                for k, v in env.items():
                    lines.append(f'{_toml_key(k)} = {json.dumps(str(v))}')
        lines.append("")

    content = "\n".join(lines).strip() + "\n"
    generated = tomllib.loads(content)
    # The line-preserving edit must also preserve the parsed meaning.
    # Unusual valid TOML layouts may be refused, never silently rewritten
    # into different settings or a missing private connector.
    if any(generated.get(key) != value for key, value in previous.items() if key != "mcp_servers"):
        raise ValueError("Codex rendering would change unrelated settings; original preserved.")
    old_servers = previous.get("mcp_servers", {})
    if not isinstance(old_servers, dict):
        raise ValueError("Codex mcp_servers must be a table; original preserved.")
    for key, value in old_servers.items():
        if key not in managed and key not in unmounted and generated.get("mcp_servers", {}).get(key) != value:
            raise ValueError("Codex rendering would change an unmanaged connector; original preserved.")
    if write:
        renderer._backup_and_write(cfg_file, content)
    return True, t("Codex configuration updated")
