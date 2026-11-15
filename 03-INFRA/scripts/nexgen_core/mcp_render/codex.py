"""Codex MCP dialect (TOML `~/.codex/config.toml`)."""
from __future__ import annotations

import json
import re
import tomllib
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.paths import codex_config

#: The per-server keys this renderer writes. Every other key of a managed section is the
#: Codex app's own state, documented in its config reference: `enabled_tools`,
#: `disabled_tools`, `default_tools_approval_mode`, `tools.<tool>.approval_mode`.
_OWNED_KEYS = frozenset({
    "command", "args", "url", "bearer_token_env_var", "env",
    "startup_timeout_sec", "tool_timeout_sec",
})


def _toml_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name)


def _toml_value(value: Any) -> str:
    """One TOML literal for a key the app owns, in the shapes its own file uses."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ValueError(f"Cannot write {value!r} back into the Codex configuration")


def _toml_table(path: str, values: dict[str, Any]) -> list[str]:
    """A TOML table header, its scalar keys, then a sub-table for every nested object."""
    lines = [f"[{path}]"]
    lines.extend(f"{_toml_key(key)} = {_toml_value(value)}"
                 for key, value in values.items() if not isinstance(value, dict))
    for key, table in values.items():
        if isinstance(table, dict):
            lines.append("")
            lines.extend(_toml_table(f"{path}.{_toml_key(key)}", table))
    return lines


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


def _build_content(renderer, servers, existing_lines, preserved, previous):
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
    # Deduplicated, keeping the first occurrence: two manifest names that collapse
    # into the same section (`demo-one` and `demo_one`) would otherwise be emitted
    # twice, and `tomllib.loads` below refuses a table declared twice.
    manifest_order = list(dict.fromkeys(name.replace("-", "_") for name in renderer.manifest_server_names()))
    for section_name, block in preserved.items():
        if section_name not in manifest_order:
            lines.append("\n".join(block).strip())
            lines.append("")
    by_safe_name = {name.replace("-", "_"): (name, srv) for name, srv in servers.items()}
    # Codex writes `-` as `_`, so a section rendered under the other spelling still
    # carries the state the app set on it. A `mcp_servers` that is not a table is
    # refused further down, by `_validate_unchanged`.
    on_disk = previous.get("mcp_servers")
    previous_by_safe = {
        str(key).replace("-", "_"): entry
        for key, entry in (on_disk if isinstance(on_disk, dict) else {}).items()
        if isinstance(entry, dict)
    }
    for safe_name in manifest_order + [n for n in by_safe_name if n not in manifest_order]:
        if safe_name in by_safe_name:
            lines.extend(_render_server(*by_safe_name[safe_name], previous_by_safe.get(safe_name)))
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
    deleting it. Only retirement removes entries, by tombstone or because
    the engine declared the name at an earlier render and no longer does;
    manifest servers are always re-emitted fresh.
    """
    servers = renderer.load_resolved_servers("codex")
    cfg_file = codex_config(renderer.home)
    retired = renderer.retired_server_names()
    unmounted, managed = _managed_sets(renderer, servers, retired)
    existing_lines, preserved, previous = _load_previous(cfg_file, managed, unmounted)
    content, generated = _build_content(renderer, servers, existing_lines, preserved, previous)
    _validate_unchanged(generated, previous, managed, unmounted)
    if write:
        renderer._backup_and_write(cfg_file, content)
    return True, t("Codex configuration updated")


def _render_server(name: str, srv: dict, previous: dict | None = None) -> list[str]:
    """One manifest server as its TOML section(s), freshly rendered.

    A managed section is re-emitted, but the keys the app owns and this renderer does
    not write are carried over from `previous`: dropping them would switch back on a
    server the Codex app disabled, and lose the tool allow and deny lists set there.
    """
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
    # The app's own keys of this section, re-emitted before the env sub-table: a key
    # written after a `[table]` header belongs to that table.
    kept = {key: value for key, value in (previous or {}).items() if key not in _OWNED_KEYS}
    lines.extend(f"{_toml_key(key)} = {_toml_value(value)}"
                 for key, value in kept.items() if not isinstance(value, dict))
    if not (srv.get("transport") == "http" or srv.get("url")):
        env = srv.get("env", {})
        if env:
            lines.append(f"[mcp_servers.{section}.env]")
            for k, v in env.items():
                lines.append(f'{_toml_key(k)} = {json.dumps(str(v))}')
    for key, table in kept.items():
        if isinstance(table, dict):
            lines.append("")
            lines.extend(_toml_table(f"mcp_servers.{section}.{_toml_key(key)}", table))
    lines.append("")
    return lines
