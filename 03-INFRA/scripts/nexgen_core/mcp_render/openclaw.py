"""OpenClaw MCP dialect (`~/.openclaw/openclaw.json`, `mcp.servers`).

OpenClaw is the harness with the interface (web panel, phone app) through
which the other CLIs and the user's subscriptions are used, and its
configuration file belongs to it and to its owner: this module writes the
server table under `mcp.servers` and nothing else. Three of its schema rules
are not guessable from the shape of the file, and each one is enforced here
because ignoring it turns into a connector that silently never starts:

- the URL is validated before `${VAR}` substitution, so an unresolved
  reference is refused at write time: the URL is written resolved, and a
  server whose variable is missing is left alone instead;
- an http server needs `"transport": "streamable-http"`, or the handshake
  times out with nothing in the log;
- a Python server on Windows dies with `UnicodeEncodeError` on the first
  accented byte, so every Python command gets a UTF-8 environment.
"""
from __future__ import annotations

import copy
import logging
import math
import re
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.mcp_render import load_json_object, write_json_config
from nexgen_core.paths import openclaw_config

logger = logging.getLogger("nexgen.mcp.openclaw")

#: An interpreter this module must give a UTF-8 environment to: `python`,
#: `python3`, `python3.12`, `py`, and the same names as a Windows `.exe`,
#: including the copy a virtualenv keeps under `Scripts/`.
_PYTHON_COMMAND = re.compile(r"^(python[0-9.]*|py)(\.exe)?$", re.I)

#: OpenClaw's own name for the transport behind an http MCP endpoint.
STREAMABLE_HTTP = "streamable-http"


def _is_python(command: str) -> bool:
    """Whether this command line starts a Python interpreter."""
    return bool(_PYTHON_COMMAND.match(Path(command).name))


def _stdio_entry(srv: dict[str, Any], references: dict[str, str] | None = None) -> dict[str, Any]:
    """One locally launched server, in OpenClaw's stdio shape.

    `references` are the env values the manifest wrote as a bare `${VAR}`:
    OpenClaw substitutes those itself, so writing the resolved value would put
    a secret that lives in the environment into `openclaw.json` in plain text.
    """
    command = str(srv.get("command") or "")
    raw_env = srv.get("env") if isinstance(srv.get("env"), dict) else {}
    env = {str(k): str(v) for k, v in raw_env.items()}
    for key, reference in (references or {}).items():
        if key in env:
            env[key] = reference
    if _is_python(command):
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
    raw_args = srv.get("args") if isinstance(srv.get("args"), list) else []
    entry: dict[str, Any] = {"command": command, "args": [str(a) for a in raw_args]}
    if env:
        entry["env"] = env
    return entry


#: A numeric number of seconds, as YAML hands it over when it was quoted.
_NUMERIC_SECONDS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def _millis(timeouts: Any, key: str) -> int | None:
    """A manifest timeout (seconds) as the positive milliseconds OpenClaw wants.

    A value that is not a number is no timeout, never a crash: the field is
    optional and a broken one must not stop the other servers from mounting.
    """
    raw = timeouts.get(key) if isinstance(timeouts, dict) else None
    match = _NUMERIC_SECONDS.match(raw) if isinstance(raw, str) else None
    if match:
        millis = int(float(match.group(1)) * 1000)
    elif isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw):
        millis = int(float(raw) * 1000)
    else:
        return None
    return millis if millis > 0 else None


def _http_entry(srv: dict[str, Any]) -> dict[str, Any]:
    """One remote server, in OpenClaw's http shape (timeouts already in ms)."""
    entry: dict[str, Any] = {"url": str(srv["url"]), "transport": STREAMABLE_HTTP}
    auth = srv.get("auth")
    auth_env = auth.get("env") if isinstance(auth, dict) else None
    if auth_env:
        # A reference, never a value: OpenClaw substitutes `${NAME}` in its strings.
        entry["headers"] = {"Authorization": f"Bearer ${{{auth_env}}}"}
    timeouts = srv.get("timeouts")
    connection = _millis(timeouts, "startup")
    request = _millis(timeouts, "tool")
    if connection is not None:
        entry["connectionTimeoutMs"] = connection
    if request is not None:
        entry["requestTimeoutMs"] = request
    return entry


def _build_entry(srv: dict[str, Any], references: dict[str, str] | None = None) -> dict[str, Any]:
    """One manifest server as its native OpenClaw entry."""
    is_http = srv.get("transport") == "http" or bool(srv.get("url"))
    entry = _http_entry(srv) if is_http else _stdio_entry(srv, references)
    deny = srv.get("tools_deny")
    if isinstance(deny, list) and deny:
        entry["toolFilter"] = {"exclude": [str(tool) for tool in deny]}
    return entry


def _resolved_url(srv: dict[str, Any]) -> str | None:
    """The URL to write, or None when it still carries an unresolved reference.

    OpenClaw validates the URL before substituting variables, so `${VAR}` in
    that field is refused outright: writing the resolved value is the only
    shape it accepts, and a missing variable means the server is skipped the
    same way `require_env` skips one.
    """
    url = str(srv.get("url") or "").strip()
    return url if url and "${" not in url else None


def _mcp_block(existing: dict[str, Any], cfg_file: Path) -> dict[str, Any]:
    """The file's `mcp` object, which OpenClaw also uses for its own settings.

    A file that declares no `mcp` block at all is the normal shape of a fresh
    install, not an error: the table is added, and everything else in the file
    stays as it is.
    """
    raw_mcp = existing.get("mcp", {})
    if not isinstance(raw_mcp, dict):
        raise ValueError(f"Could not render {cfg_file}: mcp must be an object")
    servers = raw_mcp.get("servers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"Could not render {cfg_file}: mcp.servers must be an object")
    block = dict(raw_mcp)
    block["servers"] = servers
    return block


def _apply_servers(
    renderer,
    servers_table: dict[str, Any],
    servers: dict[str, dict[str, Any]],
    references: dict[str, dict[str, str]] | None = None,
) -> None:
    """Mounts every resolved server and drops the ones this engine retired."""
    for retired in renderer.retired_server_names():
        servers_table.pop(retired, None)
    renderer._drop_unmounted(servers_table, servers, cli_target="openclaw")
    for name, srv in servers.items():
        is_http = srv.get("transport") == "http" or bool(srv.get("url"))
        if is_http and _resolved_url(srv) is None:
            logger.warning("OpenClaw connector %r has a URL this machine cannot resolve, skipped", name)
            continue
        servers_table[name] = _build_entry(srv, (references or {}).get(name))


#: The variables the engine itself resolves to a path or a port. Their value
#: must arrive in `openclaw.json` already written: the gateway's environment is
#: not the engine's, so an unresolved path would break the server it configures.
ENGINE_RESOLVED_VARS = frozenset({
    "AGENT_ENGINE_ROOT",
    "AGENT_VAULT_DATA",
    "KNOWLEDGE_VAULT_PATH",
    "OCR_TUNNEL_PORT",
    "N8N_TUNNEL_PORT",
})

#: One `${NAME}` or `${NAME:-default}` reference inside a string value.
_REFERENCE_NAME = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")


def _env_references(renderer) -> dict[str, dict[str, str]]:
    """The manifest env values OpenClaw must receive exactly as written.

    Every `${VAR}` stays a reference, because OpenClaw substitutes it itself
    and a credential must not be copied out of the environment into
    `openclaw.json`. The exception is a value whose references are all
    engine-owned paths or ports: those are written resolved, because the
    gateway does not inherit the engine's environment. A value mixing the two
    stays a reference: leaking a secret is worse than a port that may be
    missing.
    """
    from nexgen_core.config import load_mcp_manifest
    from nexgen_core.mcp_render import IS_WINDOWS

    if not renderer.manifest_path.is_file():
        return {}
    try:
        data = load_mcp_manifest(renderer.manifest_path)
    except (OSError, ValueError):  # nexgen: allow reason="a broken manifest is reported by mcp.manifest; this render writes no reference rather than crashing"
        return {}
    declared = data.get("servers") if isinstance(data.get("servers"), dict) else {}
    found: dict[str, dict[str, str]] = {}
    for name, srv in declared.items():
        if not isinstance(srv, dict):
            continue
        raw = srv.get("env") if isinstance(srv.get("env"), dict) else {}
        override = srv.get("windows") if IS_WINDOWS else None
        if isinstance(override, dict) and isinstance(override.get("env"), dict):
            raw = override["env"]  # the renderer replaces the whole block, it does not merge it
        kept: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(value, str) or "${" not in value or "{{" in value:
                continue
            names = set(_REFERENCE_NAME.findall(value))
            if names and names <= ENGINE_RESOLVED_VARS:
                continue
            kept[str(key)] = value
        if kept:
            found[str(name)] = kept
    return found


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Writes the manifest's connectors into OpenClaw's `mcp.servers` table.

    Nothing else in the file is touched: the gateway settings, the models and
    the channels are OpenClaw's own, and a server the manifest does not
    declare is preserved. A machine where OpenClaw was never installed gets
    no file, because this engine patches configurations that exist rather
    than inventing them.
    """
    cfg_file = openclaw_config(renderer.home)
    if not cfg_file.is_file():
        return True, t("OpenClaw configuration not present: nothing rendered for it")
    servers = renderer.load_resolved_servers("openclaw")
    existing = load_json_object(cfg_file)
    before = copy.deepcopy(existing)
    mcp_config = _mcp_block(existing, cfg_file)
    servers_table = dict(mcp_config["servers"])
    _apply_servers(renderer, servers_table, servers, _env_references(renderer))
    mcp_config["servers"] = servers_table
    existing["mcp"] = mcp_config
    if write:
        write_json_config(renderer, cfg_file, before, existing)
    return True, t("OpenClaw configuration updated")
