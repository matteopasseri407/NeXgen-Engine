"""DeepSeek Harness MCP dialect (`<dsh_home>/profiles/<profile>/cordis.patch.yml`).

DSH composes a profile from its bundle layers and then applies this user-owned
patch: a top-level YAML array of `- id:` entries. The engine owns exactly the
`mcp-manager` entry and the three privacy switches; every other id belongs to
the person or to the app's Settings page, which writes the same document, and
survives untouched and in place.

Two rules of the harness decide the shape written here, and both cost a
connector when ignored:

- a `- id: mcp-manager` entry replaces the WHOLE `servers` map, so the live
  table is read back, only the names this engine owns are dropped, and every
  server the person added in Settings is mounted again beside them;
- a stdio child starts with a scrubbed environment: every inherited variable
  whose name contains KEY, PASSWORD, SECRET or TOKEN, and every `DSH_*`, is
  removed before the entry's own `env` is applied. A credential therefore
  travels as the harness's own template, `${credential:NAME}`, never as a
  value, and every such name a server reads -- including the ones behind
  `lazy-mcp`, whose children inherit the gateway's environment -- is written
  explicitly in the entry that starts it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from nexgen_core.i18n import t
from nexgen_core.mcp_placement import GATEWAY
from nexgen_core.mcp_render import is_python_command, merge_entry, milliseconds
from nexgen_core.paths import dsh_profile_patch
from nexgen_core.runtimes.dsh import DshRuntime

#: The profile the desktop app composes for itself. The `headless` one belongs to
#: one-shot runs and is written per call by the Council seat, never by a sync.
PROFILE = "desktop"

MCP_ID = "mcp-manager"

#: The privacy switches the harness ships ON and this layer turns OFF for good.
#: Written on every render so a settings reset cannot quietly re-enable an upload.
_PRIVACY: dict[str, dict[str, Any]] = {
    "session-log-deepseek": {"enabled": False},
    "session-telemetry-otel": {"mode": "DISABLED"},
    "plugin-package-inventory-deepseek": {"enabled": False},
}

#: The per-server keys this renderer writes. Everything else in a live entry --
#: the switch the Settings page flips, whatever the manager adds later -- is the
#: person's or the app's state and survives a render.
_OWNED_SERVER_KEYS = frozenset({
    "transport", "command", "args", "env", "url", "headers",
    "startupTimeoutMs", "toolCallTimeoutMs",
})

#: The order a mounted entry's keys are written in. A canonical order is what
#: makes a second render byte-identical: a YAML round-trip alone would keep
#: whichever order the previous file happened to have, so a key preserved from
#: the file would drift to the front once and stay there.
_SERVER_KEY_ORDER = (
    "transport", "command", "args", "url", "headers", "enabled", "env",
    "startupTimeoutMs", "toolCallTimeoutMs",
)

#: The harness's own name for an HTTP MCP endpoint.
STREAMABLE_HTTP = "streamable-http"

#: What the harness removes from a child's inherited environment: any name
#: containing one of these (case-insensitive), plus its own `DSH_*` variables.
_SCRUBBED = re.compile(r"(KEY|PASSWORD|SECRET|TOKEN)", re.I)
_DSH_PREFIX = "DSH_"

#: One `${NAME}` or `${NAME:-default}` reference inside a manifest string.
_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")


def _entry_id(entry: Any) -> str | None:
    """The id a patch entry targets, or None for anything that is not one."""
    if isinstance(entry, dict):
        value = entry.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _leading_comments(text: str) -> str:
    """The comment block above the first entry, which a YAML round-trip drops.

    The shipped patch opens with two lines explaining the layer; the person may
    add their own there. They are carried over verbatim instead of vanishing at
    the first sync, which reads as the engine having overwritten the file.
    """
    kept: list[str] = []
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            break
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + "\n" if kept else ""


def _load_patch(path: Path) -> tuple[list[Any], str]:
    """The patch entries and the text they came from; a wrong root stops the render.

    A file that exists but is not a YAML list is not repaired and not replaced:
    writing "our" array over it would discard a document the app or the person
    shaped, so the render names the file and stops.
    """
    raw = path.read_text(encoding="utf-8-sig")
    try:
        data = yaml.safe_load(raw) if raw.strip() else []
    except yaml.YAMLError as exc:
        raise ValueError(f"Could not parse {path}: invalid YAML ({exc})") from exc
    if data is None:
        return [], raw
    if not isinstance(data, list):
        raise ValueError(f"Could not parse {path}: expected a YAML list of patch entries")
    return data, raw


def _servers_in(entries: list[Any], path: Path) -> dict[str, Any]:
    """The live `mcp-manager` entry's server table, or an empty one when none is declared."""
    for entry in entries:
        if _entry_id(entry) != MCP_ID:
            continue
        config = entry.get("config")
        if config is None:
            return {}
        if not isinstance(config, dict):
            raise ValueError(f"Could not render {path}: the mcp-manager entry's config must be a mapping")
        servers = config.get("servers", {})
        if not isinstance(servers, dict):
            raise ValueError(f"Could not render {path}: mcp-manager config.servers must be a mapping")
        return dict(servers)
    return {}


def _platform_entry(srv: dict[str, Any], windows: bool) -> dict[str, Any]:
    """One manifest entry with its Windows override applied, the way resolution does it."""
    override = srv.get("windows") if windows else None
    return {**srv, **override} if isinstance(override, dict) else srv


def _references(srv: dict[str, Any]) -> set[str]:
    """Every environment variable name this manifest entry reads.

    Names can arrive three ways, and all three matter: a bare name (`require_env`,
    `url_env`, `auth.env`), a `${VAR}` reference inside a value, and a reference
    inside an argument or the URL.
    """
    found: set[str] = set()
    for key in ("require_env", "url_env"):
        value = srv.get(key)
        if isinstance(value, str) and value.strip():
            found.add(value.strip())
    auth = srv.get("auth")
    if isinstance(auth, dict) and isinstance(auth.get("env"), str) and auth["env"].strip():
        found.add(auth["env"].strip())
    strings = [srv.get("url")]
    args = srv.get("args")
    if isinstance(args, list):
        strings.extend(args)
    env = srv.get("env")
    if isinstance(env, dict):
        strings.extend(env.values())
    for value in strings:
        if isinstance(value, str):
            found.update(_REFERENCE.findall(value))
    return found


def _scrubbed_names(names: set[str]) -> set[str]:
    """The names the harness strips from a child's inherited environment."""
    return {name for name in names if name.startswith(_DSH_PREFIX) or _SCRUBBED.search(name)}


def _raw_manifest_servers(renderer) -> dict[str, Any]:
    """The manifest as written, where `${VAR}` references are still visible."""
    try:
        return renderer._manifest_servers()
    except (OSError, ValueError):  # nexgen: allow reason="a broken manifest is reported by mcp.manifest; this render then adds no credential template"
        return {}


def _gateway_references(raw: dict[str, Any], windows: bool) -> set[str]:
    """The credentials of every lazy server the gateway in this CLI starts as its children."""
    from nexgen_core.mcp_placement import gateway_servers_for

    found: set[str] = set()
    for name in gateway_servers_for(raw, "dsh"):
        srv = raw.get(name)
        if isinstance(srv, dict):
            found |= _references(_platform_entry(srv, windows))
    return found


def _credential_names(renderer, resolved: dict[str, dict[str, Any]]) -> dict[str, frozenset[str]]:
    """The scrubbed names each mounted server reads, its gateway children included."""
    raw = _raw_manifest_servers(renderer)
    windows = renderer.is_windows
    gateway = _scrubbed_names(_gateway_references(raw, windows))
    names: dict[str, frozenset[str]] = {}
    for name in resolved:
        declared = raw.get(name)
        found = _references(_platform_entry(declared, windows)) if isinstance(declared, dict) else set()
        if name == GATEWAY:
            found |= gateway
        names[name] = frozenset(_scrubbed_names(found))
    return names


def _timeout_fields(srv: dict[str, Any]) -> dict[str, int]:
    """The manifest's timeouts in the milliseconds the manager's schema expects."""
    fields: dict[str, int] = {}
    startup = milliseconds(srv.get("timeouts"), "startup")
    tool = milliseconds(srv.get("timeouts"), "tool")
    if startup is not None:
        fields["startupTimeoutMs"] = startup
    if tool is not None:
        fields["toolCallTimeoutMs"] = tool
    return fields


def _stdio_entry(srv: dict[str, Any], names: frozenset[str]) -> dict[str, Any]:
    """One locally launched server, with its credentials as `${credential:NAME}` templates."""
    command = str(srv.get("command") or "")
    raw_args = srv.get("args") if isinstance(srv.get("args"), list) else []
    raw_env = srv.get("env") if isinstance(srv.get("env"), dict) else {}
    env = {str(key): str(value) for key, value in raw_env.items()}
    if is_python_command(command):
        # A Python server on Windows dies on its first accented byte without this,
        # exactly as it does under OpenClaw's spawner.
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
    for name in sorted(names):
        env[name] = f"${{credential:{name}}}"
    entry: dict[str, Any] = {
        "transport": "stdio",
        "command": command,
        "args": [str(arg) for arg in raw_args],
    }
    if env:
        entry["env"] = env
    entry.update(_timeout_fields(srv))
    return entry


def _http_entry(srv: dict[str, Any]) -> dict[str, Any]:
    """One remote server; the bearer is a template the harness resolves, never a value."""
    entry: dict[str, Any] = {
        "transport": STREAMABLE_HTTP,
        "url": str(srv.get("url") or ""),
    }
    auth = srv.get("auth")
    auth_env = auth.get("env") if isinstance(auth, dict) else None
    if isinstance(auth_env, str) and auth_env.strip():
        entry["headers"] = {"Authorization": f"Bearer ${{credential:{auth_env.strip()}}}"}
    entry.update(_timeout_fields(srv))
    return entry


def _build_entry(srv: dict[str, Any], names: frozenset[str]) -> dict[str, Any]:
    """One manifest server as its native patch entry."""
    is_http = srv.get("transport") == "http" or bool(srv.get("url"))
    return _http_entry(srv) if is_http else _stdio_entry(srv, names)


def _ordered_entry(merged: dict[str, Any]) -> dict[str, Any]:
    """The entry with its keys in the one canonical order a second render reproduces."""
    ordered = {key: merged[key] for key in _SERVER_KEY_ORDER if key in merged}
    ordered.update({key: value for key, value in merged.items() if key not in ordered})
    return ordered


def _mount_servers(
    renderer,
    table: dict[str, Any],
    resolved: dict[str, dict[str, Any]],
    names: dict[str, frozenset[str]],
) -> dict[str, Any]:
    """Mounts every resolved server and drops only the names this engine owns."""
    for retired in renderer.retired_server_names():
        table.pop(retired, None)
    renderer._drop_unmounted(table, resolved, cli_target="dsh")
    for name, srv in resolved.items():
        entry = _build_entry(srv, names.get(name, frozenset()))
        merged = merge_entry(table.get(name), entry, _OWNED_SERVER_KEYS)
        # `enabled` is the person's switch, so it is never rewritten; a server
        # that has none is mounted on, which is what the manager's own screen shows.
        merged.setdefault("enabled", True)
        table[name] = _ordered_entry(merged)
    return table


def _privacy_entry(existing: Any, entry_id: str) -> dict[str, Any]:
    """One privacy switch, in its place if the person already wrote it, with the value forced off."""
    entry = dict(existing) if isinstance(existing, dict) else {}
    config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    entry["id"] = entry_id
    entry["config"] = {**config, **_PRIVACY[entry_id]}
    return entry


def _mcp_entry(existing: Any, servers: dict[str, Any]) -> dict[str, Any]:
    """The `mcp-manager` entry, keeping every key of its config that is not the server table."""
    entry = dict(existing) if isinstance(existing, dict) else {}
    config = dict(entry["config"]) if isinstance(entry.get("config"), dict) else {}
    entry["id"] = MCP_ID
    config["servers"] = servers
    entry["config"] = config
    return entry


def _engine_entries(entries: list[Any], servers: dict[str, Any]) -> list[Any]:
    """The patch with the engine's entries replaced in place and the missing ones appended."""
    kept: list[Any] = []
    seen: set[str] = set()
    for entry in entries:
        entry_id = _entry_id(entry)
        if entry_id == MCP_ID:
            kept.append(_mcp_entry(entry, servers))
        elif entry_id in _PRIVACY:
            kept.append(_privacy_entry(entry, entry_id))
        else:
            kept.append(entry)
        if entry_id:
            seen.add(entry_id)
    if MCP_ID not in seen:
        kept.append(_mcp_entry(None, servers))
    for entry_id in _PRIVACY:
        if entry_id not in seen:
            kept.append(_privacy_entry(None, entry_id))
    return kept


def _dump(entries: list[Any], comments: str) -> str:
    """The patch text: the preserved head comments, then the array in a stable order."""
    body = yaml.safe_dump(
        entries,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )
    return comments + body


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Writes the manifest's connectors into the desktop profile's patch, additively.

    The file is patched, never invented: a machine where DSH is absent, or where
    the desktop profile was never materialized, gets nothing and no `~/.dsh`.
    """
    if not DshRuntime().is_installed(renderer.home):
        return True, t("DeepSeek Harness is not installed: nothing rendered for it")
    patch = dsh_profile_patch(renderer.home, PROFILE)
    if not patch.is_file():
        return True, t("DeepSeek Harness profile patch not present: nothing rendered for it")
    entries, raw = _load_patch(patch)
    resolved = renderer.load_resolved_servers("dsh")
    names = _credential_names(renderer, resolved)
    table = _mount_servers(renderer, _servers_in(entries, patch), resolved, names)
    content = _dump(_engine_entries(entries, table), _leading_comments(raw))
    if write:
        renderer._backup_and_write(patch, content)
    return True, t("DeepSeek Harness configuration updated")
