"""Codex MCP dialect (TOML `~/.codex/config.toml`)."""
from __future__ import annotations

import json
import re
import tomllib

from nexgen_core.i18n import t
from nexgen_core.paths import codex_config


def _toml_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name)


def _managed_sets(renderer, servers, retired) -> tuple[set, set]:
    normalized: dict[str, str] = {}
    for name in list(servers):
        safe = name.replace("-", "_")
        if safe in normalized and normalized[safe] != name:
            raise ValueError(
                f"Codex connector collision: {normalized[safe]!r} and {name!r} map to the same section {safe!r}"
            )
        normalized[safe] = name
    # A retired/active pair with the same normalized name is a rename:
    # the retired entry only removes the old section, it must not abort the render.
    unmounted = {
        name.replace("-", "_")
        for name in renderer.unmounted_server_names(servers, "codex")
    }
    managed = {name.replace("-", "_") for name in servers} | {name.replace("-", "_") for name in retired}
    return unmounted, managed


def _load_previous(cfg_file, managed, unmounted):
    existing_lines: list[str] = []
    # Kept sections grouped by name, so each one can be emitted at a fixed
    # place whatever the environment resolved this time (see below).
    preserved: dict[str, list[str]] = {}
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
                    # A hand-written `my-server` is the manifest's `my_server`:
                    # compare in Codex's spelling, or both end up in the file.
                    keep_current = section.replace("-", "_") not in managed and section.replace("-", "_") not in unmounted
                    if keep_current:
                        preserved.setdefault(section, []).append(line)
                    continue
                elif stripped.startswith("[") and not stripped.startswith("[mcp_servers."):
                    in_mcp_section = False
                    keep_current = False
                if not in_mcp_section and not line.startswith("# NeXgen Engine"):
                    existing_lines.append(line)
                elif in_mcp_section and keep_current:
                    preserved[section].append(line)
        except (OSError, ValueError) as exc:
            raise ValueError("Cannot read or parse the Codex configuration; original preserved.") from exc
    return existing_lines, preserved, previous


def _build_content(renderer, servers, existing_lines, preserved):
    header = "# NeXgen Engine - Codex MCP configuration, auto-generated"
    lines: list[str] = []
    if existing_lines:
        cleaned_existing = "\n".join(existing_lines).strip()
        if cleaned_existing:
            lines.append(cleaned_existing)
            lines.append("")

    lines.append(header)
    lines.append("")
    # One fixed order, independent of os.environ: connectors the manifest
    # does not know first (as found on disk), then every manifest server in
    # manifest order. An env-gated server that cannot be resolved right now
    # (the guard runs without the user's shell environment) keeps its text
    # from disk but also its manifest slot. Putting it among the foreign
    # connectors made the guard and an interactive apply reorder the file
    # against each other on every run: endless rewrites, backups and a
    # drift warning that never cleared.
    manifest_order = [name.replace("-", "_") for name in renderer.manifest_server_names()]
    for section_name, block in preserved.items():
        if section_name not in manifest_order:
            lines.append("\n".join(block).strip())
            lines.append("")
    by_safe_name = {name.replace("-", "_"): (name, srv) for name, srv in servers.items()}
    for safe_name in manifest_order + [n for n in by_safe_name if n not in manifest_order]:
        if safe_name in by_safe_name:
            lines.extend(_render_server(*by_safe_name[safe_name]))
        elif safe_name in preserved:
            lines.append("\n".join(preserved[safe_name]).strip())
            lines.append("")

    content = "\n".join(lines).strip() + "\n"
    generated = tomllib.loads(content)
    return content, generated


def _validate_unchanged(generated, previous, managed, unmounted) -> None:
    # The line-preserving edit must also preserve the parsed meaning.
    # Unusual valid TOML layouts may be refused, never silently rewritten
    # into different settings or a missing private connector.
    if any(generated.get(key) != value for key, value in previous.items() if key != "mcp_servers"):
        raise ValueError("Codex rendering would change unrelated settings; original preserved.")
    old_servers = previous.get("mcp_servers", {})
    if not isinstance(old_servers, dict):
        raise ValueError("Codex mcp_servers must be a table; original preserved.")
    for key, value in old_servers.items():
        if (key.replace("-", "_") not in managed and key.replace("-", "_") not in unmounted
                and generated.get("mcp_servers", {}).get(key) != value):
            raise ValueError("Codex rendering would change an unmanaged connector; original preserved.")


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
    unmounted, managed = _managed_sets(renderer, servers, retired)
    existing_lines, preserved, previous = _load_previous(cfg_file, managed, unmounted)
    content, generated = _build_content(renderer, servers, existing_lines, preserved)
    _validate_unchanged(generated, previous, managed, unmounted)
    if write:
        renderer._backup_and_write(cfg_file, content)
    return True, t("Codex configuration updated")


def _render_server(name: str, srv: dict) -> list[str]:
    """One manifest server as its TOML section(s), freshly rendered."""
    lines: list[str] = []
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
    return lines
