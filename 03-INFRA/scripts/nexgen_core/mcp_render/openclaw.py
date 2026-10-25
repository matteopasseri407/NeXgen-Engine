"""OpenClaw MCP dialect (`~/.openclaw/openclaw.json`, `mcp.servers`).

OpenClaw is the harness with the interface (web panel, phone app) through
which the other CLIs and the user's subscriptions are used, and its
configuration file belongs to it and to its owner: this module writes the
server table under `mcp.servers` and nothing else. Three of its schema rules
are not guessable from the shape of the file, and each one is enforced here
because ignoring it turns into a connector that silently never starts:

- the URL is validated before `${VAR}` substitution, so an unresolved
  reference is refused at write time: an HTTP server with a secret URL
  (`${VAR}` in manifest or `url_env`) is routed as stdio through
  `mcp-http-bridge.mjs` where OpenClaw safely expands `${VAR}` in args;
  literal HTTP URLs are written resolved with `"transport": "streamable-http"`;
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
from nexgen_core.mcp_render import IS_WINDOWS, MCP_REMOTE_PACKAGE, load_json_object, write_json_config
from nexgen_core.paths import openclaw_config

logger = logging.getLogger("nexgen.mcp.openclaw")

#: An interpreter this module must give a UTF-8 environment to: `python`,
#: `python3`, `python3.12`, `py`, and the same names as a Windows `.exe`,
#: including the copy a virtualenv keeps under `Scripts/`.
_PYTHON_COMMAND = re.compile(r"^(python[0-9.]*|py)(\.exe)?$", re.I)

#: OpenClaw's own name for the transport behind an http MCP endpoint.
STREAMABLE_HTTP = "streamable-http"

#: The variable the local bridge's bearer token lives in, as its owner names it
#: (`nexgen_core.mcp_bridge.TOKEN_ENV`). A bridge entry the engine builds always
#: says which variable to use; this is the fallback for a hand-written map.
_BRIDGE_TOKEN_ENV = "NEXGEN_MCP_BRIDGE_TOKEN"


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


def _bridge_target(name: str, bridge_spec: Any) -> tuple[str, str]:
    """`(url, token variable)` from what the engine asked for: a plain URL or `{url, token_env}`."""
    if isinstance(bridge_spec, dict):
        url = str(bridge_spec.get("url") or "").strip()
        token_env = str(bridge_spec.get("token_env") or _BRIDGE_TOKEN_ENV)
    else:
        url = str(bridge_spec or "").strip()
        token_env = _BRIDGE_TOKEN_ENV
    if not url:
        raise ValueError(f"Could not render {name}: the bridge entry has no url")
    return url, token_env


def _bridged_entry(name: str, srv: dict[str, Any], bridge_spec: Any) -> dict[str, Any]:
    """One connector reached over the local bridge: the same http shape, aimed at the bridge.

    Nothing else about the server changes: its timeouts and its tool filter travel
    with it, because they are the engine's contract with that connector and the
    transport it is reached over is an implementation detail.
    """
    url, token_env = _bridge_target(name, bridge_spec)
    bridged = {**srv, "url": url, "transport": "http", "auth": {"type": "bearer", "env": token_env}}
    return _build_entry(bridged)


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


def is_engine_lazy_mcp(entry: Any) -> bool:
    """Whether an mcp.servers entry for lazy-mcp was written by the engine."""
    if not isinstance(entry, dict):
        return False
    args = [str(a) for a in entry.get("args") or []]
    cmd = str(entry.get("command") or "")
    env = entry.get("env") if isinstance(entry.get("env"), dict) else {}
    if "LAZY_MCP_CLI" in env:
        return True
    return any("lazy-mcp.py" in a for a in args) or "lazy-mcp.py" in cmd


def is_engine_bridge(entry: Any) -> bool:
    """Whether an mcp.servers entry is an HTTP bridge written by the engine."""
    if not isinstance(entry, dict):
        return False
    args = [str(a) for a in entry.get("args") or []]
    cmd = str(entry.get("command") or "")
    return any("mcp-http-bridge.mjs" in a for a in args) or "mcp-http-bridge.mjs" in cmd


def _secret_bridge_url(name: str, srv: dict[str, Any], raw_servers: dict[str, Any]) -> str | None:
    """The manifest's URL reference when an HTTP server has a secret or variable URL.

    OpenClaw validates `mcp.servers.*.url` before substituting environment variables,
    so `${VAR}` directly in that field is rejected. A secret URL declared as an
    environment variable (in `url_env` or as `${VAR}`) must not be written in cleartext
    into `openclaw.json`. The engine instead routes it through `mcp-http-bridge.mjs`,
    where OpenClaw performs safe runtime variable substitution on stdio arguments.
    A server whose URL variable is missing in this environment is skipped the same
    way require_env skips one.
    """
    import os

    raw_srv = raw_servers.get(name) or {}
    if IS_WINDOWS and isinstance(raw_srv.get("windows"), dict):
        win_override = raw_srv["windows"]
        if isinstance(win_override, dict):
            raw_srv = {**raw_srv, **win_override}

    url_env = raw_srv.get("url_env")
    if isinstance(url_env, str) and url_env.strip():
        var_name = url_env.strip()
        if not os.environ.get(var_name):
            return None
        return f"${{{var_name}}}"

    raw_url = str(raw_srv.get("url") or srv.get("url") or "").strip()
    if "${" in raw_url:
        refs = set(_REFERENCE_NAME.findall(raw_url))
        if refs and not (refs <= ENGINE_RESOLVED_VARS):
            if any(not os.environ.get(r) for r in refs if r not in ENGINE_RESOLVED_VARS):
                return None
            return raw_url
    return None


def _bridge_stdio_entry(
    renderer,
    url_arg: str,
    srv: dict[str, Any],
    references: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A remote server routed via the local mcp-http-bridge stdio script.

    The shape mirrors Antigravity's bridge configuration: node launcher, bridge script,
    URL reference, token environment variable name (if bearer auth), and the pinned
    mcp-remote package. OpenClaw substitutes `${VAR}` in arguments at child spawn time.
    The child process receives the token and runtime path without exposing secrets.
    """
    bridge_script = renderer.engine_root / "agent-universal-layer" / "mcp" / "mcp-http-bridge.mjs"
    auth = srv.get("auth") if isinstance(srv.get("auth"), dict) else {}
    auth_type = auth.get("type", "bearer")
    auth_env = str(auth.get("env") or "") if auth_type == "bearer" else ""
    node_cmd = "node.exe" if IS_WINDOWS else "node"
    entry: dict[str, Any] = {
        "command": node_cmd,
        "args": [str(bridge_script), url_arg, auth_env, MCP_REMOTE_PACKAGE],
    }
    env: dict[str, str] = {
        "PATH": "${PATH}",
        "HOME": "${HOME}",
    }
    if IS_WINDOWS:
        env["USERPROFILE"] = "${USERPROFILE}"
        env["APPDATA"] = "${APPDATA}"
        env["SystemRoot"] = "${SYSTEMROOT}"  # OpenClaw substitutes upper-case names only
    raw_env = srv.get("env") if isinstance(srv.get("env"), dict) else {}
    for k, v in raw_env.items():
        env[str(k)] = str(v)
    for key, reference in (references or {}).items():
        if key in env:
            env[key] = reference
    if auth_env:
        env[auth_env] = f"${{{auth_env}}}"
    entry["env"] = env
    deny = srv.get("tools_deny")
    if isinstance(deny, list) and deny:
        entry["toolFilter"] = {"exclude": [str(tool) for tool in deny]}
    return entry


def _raw_manifest_servers(renderer) -> dict[str, dict[str, Any]]:
    """The raw manifest server definitions before environment expansion."""
    if hasattr(renderer, "_manifest_servers"):
        try:
            return renderer._manifest_servers()
        except (OSError, ValueError):  # nexgen: allow reason="a broken manifest is reported by mcp.manifest; raw servers fallback to empty dict"
            pass
    if hasattr(renderer, "manifest_path") and renderer.manifest_path.is_file():
        try:
            from nexgen_core.config import load_mcp_manifest

            data = load_mcp_manifest(renderer.manifest_path)
            if isinstance(data.get("servers"), dict):
                return data["servers"]
        except (OSError, ValueError):  # nexgen: allow reason="a broken manifest is reported by mcp.manifest; raw servers fallback to empty dict"
            pass
    return {}


def _apply_servers(
    renderer,
    servers_table: dict[str, Any],
    servers: dict[str, dict[str, Any]],
    references: dict[str, dict[str, str]] | None = None,
    http_servers: dict[str, Any] | None = None,
    raw_servers: dict[str, Any] | None = None,
) -> None:
    """Mounts every resolved server and drops the ones this engine retired."""
    for retired in renderer.retired_server_names():
        servers_table.pop(retired, None)
    servers_table.pop("openclaw", None)
    if "lazy-mcp" in servers_table and is_engine_lazy_mcp(servers_table["lazy-mcp"]):
        servers_table.pop("lazy-mcp", None)
    renderer._drop_unmounted(servers_table, servers, cli_target="openclaw")
    manifest_map = raw_servers if raw_servers is not None else _raw_manifest_servers(renderer)
    for name, srv in servers.items():
        if name in ("openclaw", "lazy-mcp"):
            continue
        bridge_spec = (http_servers or {}).get(name) if http_servers else None
        if bridge_spec is not None:
            servers_table[name] = _bridged_entry(name, srv, bridge_spec)
            continue
        is_http = srv.get("transport") == "http" or bool(srv.get("url"))
        if is_http:
            bridge_url = _secret_bridge_url(name, srv, manifest_map)
            if bridge_url is not None:
                servers_table[name] = _bridge_stdio_entry(
                    renderer, bridge_url, srv, (references or {}).get(name)
                )
                continue
            if _resolved_url(srv) is None:
                logger.warning("OpenClaw connector %r has a URL this machine cannot resolve, skipped", name)
                continue
        servers_table[name] = _build_entry(srv, (references or {}).get(name))


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


def render(
    renderer,
    write: bool = False,
    http_servers: dict[str, Any] | None = None,
) -> tuple[bool, str]:
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
    effective_http = http_servers if http_servers is not None else getattr(renderer, "openclaw_http_servers", None)
    _apply_servers(renderer, servers_table, servers, _env_references(renderer), effective_http)
    mcp_config["servers"] = servers_table
    existing["mcp"] = mcp_config
    if write:
        write_json_config(renderer, cfg_file, before, existing)
    return True, t("OpenClaw configuration updated")
