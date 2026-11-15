"""Codex MCP dialect (TOML `~/.codex/config.toml`).

Its one job: keep the `[mcp_servers.*]` sections of Codex's own TOML file in step
with the manifest, and leave every other byte of that file to the app. tomlkit
does the round-trip, so the app's tables (`[apps.*]`, `[plugins.*]`,
`[marketplaces.*]`, `notify`), its comments and its key order survive a render
instead of being rebuilt line by line.
"""
from __future__ import annotations

import tomllib
from typing import Any

import tomlkit
from tomlkit.items import Comment, Table, Whitespace
from tomlkit.toml_document import TOMLDocument

from nexgen_core.i18n import t
from nexgen_core.paths import codex_config

#: The per-server keys this renderer writes. Every other key of a managed section is the
#: Codex app's own state, documented in its config reference: `enabled_tools`,
#: `disabled_tools`, `default_tools_approval_mode`, `tools.<tool>.approval_mode`.
_OWNED_KEYS = frozenset({
    "command", "args", "url", "bearer_token_env_var", "env",
    "startup_timeout_sec", "tool_timeout_sec",
})

#: The comment that marks the engine's block, above the first `[mcp_servers.*]` section.
_HEADER = "NeXgen Engine - Codex MCP configuration, auto-generated"


def _section_name(key: str) -> str:
    """Codex writes `-` as `_`, so a section and a manifest name compare in that spelling."""
    return key.replace("-", "_")


def _managed_sets(renderer, servers, retired) -> tuple[set, set]:
    """The sections that must disappear, and the ones the engine may rewrite."""
    normalized: dict[str, str] = {}
    for name in list(servers):
        safe = _section_name(name)
        if safe in normalized and normalized[safe] != name:
            raise ValueError(
                f"Codex connector collision: {normalized[safe]!r} and {name!r} map to the same section {safe!r}"
            )
        normalized[safe] = name
    # A retired/active pair with the same normalized name is a rename:
    # the retired entry only removes the old section, it must not abort the render.
    unmounted = {
        _section_name(name)
        for name in renderer.unmounted_server_names(servers, "codex")
    }
    managed = {_section_name(name) for name in servers} | {_section_name(name) for name in retired}
    return unmounted, managed


def _load_document(cfg_file) -> tuple[TOMLDocument, dict]:
    """The file as tomlkit sees it and as Python's own parser sees it, or a refusal.

    Two parsers on purpose: the document is what gets edited and written, the plain
    data is what :func:`_validate_unchanged` compares against, so the promise is about
    what any TOML reader finds in the file afterwards.
    """
    if not cfg_file.is_file():
        return tomlkit.document(), {}
    try:
        raw = cfg_file.read_text(encoding="utf-8")
        return tomlkit.parse(raw), tomllib.loads(raw)
    except (OSError, ValueError) as exc:
        raise ValueError("Cannot read or parse the Codex configuration; original preserved.") from exc


def _servers_table(document: TOMLDocument) -> Table:
    """The `mcp_servers` table to edit, created at the end of the file when absent."""
    table = document.get("mcp_servers")
    if isinstance(table, Table):
        return table
    if table is not None:
        raise ValueError("Codex mcp_servers must be a table; original preserved.")
    table = tomlkit.table()
    document["mcp_servers"] = table
    return table


def _sections(table: Table) -> list[tuple[str, Table]]:
    """The `[mcp_servers.*]` sections in file order, with the spelling the file uses."""
    return [
        (key.key, item)
        for key, item in table.value.body
        if key is not None and isinstance(item, Table) and not item.is_super_table()
    ]


def _declared_values(srv: dict) -> dict[str, Any]:
    """The keys this renderer owns, as the manifest declares them for one server."""
    remote = bool(srv.get("transport") == "http" or srv.get("url"))
    written: dict[str, Any] = {}
    if remote:
        written["url"] = srv["url"]
        auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else None
        if auth_env:
            written["bearer_token_env_var"] = auth_env
    else:
        written["command"] = srv.get("command", "")
        written["args"] = srv.get("args", [])
    # The timeouts the manifest declares apply to both kinds of server. Only the http branch
    # wrote them, so a stdio server (vault-ocr, drive and lane: 30 s to start, up to 300 s per tool)
    # ran on Codex's defaults (10 s to start), and the manifest comment claiming "Codex renders its native
    # timeout fields" was true for half the servers.
    timeouts = srv.get("timeouts", {})
    if isinstance(timeouts, dict):
        if "startup" in timeouts:
            written["startup_timeout_sec"] = float(timeouts["startup"])
        if "tool" in timeouts:
            written["tool_timeout_sec"] = float(timeouts["tool"])
    env = srv.get("env", {})
    if env and not remote:
        table = tomlkit.table()
        for key, value in env.items():
            table[key] = str(value)
        written["env"] = table
    return written


def _server_table(current: Table | None, srv: dict) -> Table:
    """One manifest server as the table Codex reads, on top of the app's own keys.

    A managed section is re-emitted, but the keys the app owns and this renderer does
    not write are kept from `current`: dropping them would switch back on a server the
    Codex app disabled, and lose the tool allow and deny lists set there.
    """
    table = current if isinstance(current, Table) and not current.is_super_table() else tomlkit.table()
    written = _declared_values(srv)
    stale = [name for name in table.keys() if name in _OWNED_KEYS and name not in written]
    for name in stale:
        del table[name]
    for name, value in written.items():
        table[name] = value
    return table


def _stamp_header(document: TOMLDocument, table: Table) -> None:
    """Put the engine marker above the first section, once and not twice.

    tomlkit writes a table's leading comment from the table's own trivia and reads it
    back as a comment of the body that holds the section, so the marker is looked for
    in both places: that is what keeps every run from adding a copy of it.
    """
    sections = _sections(table)
    bodies = [document.body, *(section.value.body for _, section in sections)]
    if any(isinstance(item, Comment) and str(item).strip() == f"# {_HEADER}" for body in bodies for _, item in body):
        return
    if sections:
        sections[0][1].trivia.indent += f"# {_HEADER}\n"


def _separate(document: TOMLDocument, table: Table) -> None:
    """One blank line between the last section and whatever follows it in the file.

    tomlkit keeps that blank line as whitespace at the end of the deepest table of the
    block, so a table sitting below it would otherwise be glued to the section above.
    """
    content_keys = [getattr(key, "key", None) for key, item in document.body if not isinstance(item, Whitespace)]
    if not content_keys or content_keys[-1] == "mcp_servers":
        return
    last = table
    while last.value.body and isinstance(last.value.body[-1][1], Table):
        last = last.value.body[-1][1]
    tail = last.value.body[-1][1] if last.value.body else None
    if isinstance(tail, Whitespace) and "\n" in tail.value:
        return
    last.value.append(None, tomlkit.ws("\n"))


def _apply(document: TOMLDocument, servers: dict, managed: set, unmounted: set) -> None:
    """Point one parsed file at the manifest: remove what left, write what is declared."""
    table = _servers_table(document)
    resolved = {_section_name(name) for name in servers}
    on_disk = {_section_name(key): (key, section) for key, section in _sections(table)}
    for name, (key, _section) in on_disk.items():
        # `unmounted` is a manifest server this CLI must not mount, the rest of `managed`
        # that is not resolved here is retired. A section spelled with a hyphen is
        # rewritten under Codex's spelling rather than kept next to its own copy.
        if name in unmounted or (name in managed and name not in resolved) or (name in resolved and key != name):
            del table[key]
    for name, srv in servers.items():
        section = _section_name(name)
        current = on_disk.get(section)
        in_place = current is not None and current[0] == section
        written_table = _server_table(current[1] if in_place else None, srv)
        if not in_place:
            table[section] = written_table
    _stamp_header(document, table)
    _separate(document, table)


def _validate_unchanged(generated, previous, managed, unmounted) -> None:
    # The in-place edit must also preserve the parsed meaning.
    # Unusual valid TOML layouts may be refused, never silently rewritten
    # into different settings or a missing private connector.
    if any(generated.get(key) != value for key, value in previous.items() if key != "mcp_servers"):
        raise ValueError("Codex rendering would change unrelated settings; original preserved.")
    old_servers = previous.get("mcp_servers", {})
    if not isinstance(old_servers, dict):
        raise ValueError("Codex mcp_servers must be a table; original preserved.")
    for key, value in old_servers.items():
        if (_section_name(key) not in managed and _section_name(key) not in unmounted
                and generated.get("mcp_servers", {}).get(key) != value):
            raise ValueError("Codex rendering would change an unmanaged connector; original preserved.")


def _render_text(cfg_file, servers, managed, unmounted) -> str:
    """One full pass over the file as it is on disk right now: read, rebuild, verify."""
    document, previous = _load_document(cfg_file)
    _apply(document, servers, managed, unmounted)
    content = tomlkit.dumps(document)
    _validate_unchanged(tomllib.loads(content), previous, managed, unmounted)
    return content


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates Codex's native MCP configuration (~/.codex/config.toml).

    Additive like the other three CLIs: an existing server that is not in
    the manifest is preserved verbatim, and one that is env-gated but not
    resolvable right now (e.g. the recurring guard running without the
    user's shell environment) keeps the entry already on disk instead of
    deleting it. Only retirement removes entries, by tombstone or because
    the engine declared the name at an earlier render and no longer does;
    manifest servers are always re-emitted fresh.

    The Codex app saves its own tables (`[apps.*]`, `[plugins.*]`,
    `[marketplaces.*]`, `notify`) into this same file, and one of those saves can
    land between the read above and the write below: the second pass reads the
    file as it is at the last moment and rebuilds from it, so that save survives
    instead of being replaced by a render computed before it existed. It is the
    same rebase the Claude, Claude Desktop and Antigravity renders do.
    """
    servers = renderer.load_resolved_servers("codex")
    cfg_file = codex_config(renderer.home)
    retired = renderer.retired_server_names()
    unmounted, managed = _managed_sets(renderer, servers, retired)
    content = _render_text(cfg_file, servers, managed, unmounted)
    if write:
        if cfg_file.is_file() and not renderer.previewing:
            content = _render_text(cfg_file, servers, managed, unmounted)
        renderer._backup_and_write(cfg_file, content)
    return True, t("Codex configuration updated")
