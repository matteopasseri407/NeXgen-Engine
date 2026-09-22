"""OpenCode 2 MCP dialect (native `mcp.servers`, V1 migration included)."""
from __future__ import annotations

import json
import re
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc, remove_jsonc_top_level_value, set_jsonc_top_level_value


def _opencode_timeouts(timeouts: Any) -> dict[str, int]:
    """Map canonical seconds to OpenCode 2's millisecond timeout names."""
    if not isinstance(timeouts, dict):
        return {}
    mapped = {}
    for source, target in (("startup", "startup"), ("tool", "execution")):
        try:
            millis = int(float(timeouts[source]) * 1000)
            if millis > 0:
                mapped[target] = millis
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
    return mapped

def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates native OpenCode 2 MCP config and migrates flat V1 entries."""
    servers = renderer.load_resolved_servers("opencode")
    cfg_file = renderer.opencode_config_path()
    existing: dict[str, Any] = {}
    raw_existing = ""
    if cfg_file.is_file():
        raw_existing = cfg_file.read_text(encoding="utf-8")
        try:
            existing = parse_jsonc(raw_existing) if cfg_file.suffix == ".jsonc" else json.loads(raw_existing)
        except Exception as exc:
            raise ValueError(f"Could not parse {cfg_file}: invalid JSON/JSONC ({exc})")

    raw_mcp = existing.get("mcp", {})
    if not isinstance(raw_mcp, dict):
        raise ValueError(f"Could not render {cfg_file}: mcp must be an object")
    if "servers" in raw_mcp:
        if not isinstance(raw_mcp["servers"], dict):
            raise ValueError(f"Could not render {cfg_file}: mcp.servers must be an object")
        mcp_config = dict(raw_mcp)
        mcp_servers = dict(raw_mcp["servers"])
    else:
        # The old flat layout is no longer the written contract. Move
        # unknown live servers as well, normalizing fields V2 rejects.
        mcp_config = {}
        mcp_servers = {}
        for name, entry in raw_mcp.items():
            if not isinstance(entry, dict):
                raise ValueError(f"Could not migrate {cfg_file}: MCP server {name!r} must be an object")
            migrated = dict(entry)
            enabled = migrated.pop("enabled", None)
            if enabled is False:
                migrated["disabled"] = True
            if isinstance(migrated.get("timeout"), int):
                migrated["timeout"] = {"execution": migrated["timeout"]}
            oauth = migrated.get("oauth")
            if isinstance(oauth, dict):
                oauth_fields = {
                    "clientId": "client_id",
                    "clientSecret": "client_secret",
                    "callbackPort": "callback_port",
                    "redirectUri": "redirect_uri",
                    "authServerMetadataUrl": "auth_server_metadata_url",
                }
                migrated["oauth"] = {oauth_fields.get(k, k): v for k, v in oauth.items()}
            mcp_servers[name] = migrated
    for retired in renderer.retired_server_names():
        mcp_servers.pop(retired, None)
    renderer._drop_unmounted(mcp_servers, servers, cli_target="opencode")
    tools_cfg: dict[str, Any] = dict(existing.get("tools") or {})
    old_tools_cfg = dict(tools_cfg)
    permissions = existing.get("permissions", [])
    if not isinstance(permissions, list):
        raise ValueError(f"Could not render {cfg_file}: permissions must be an array")
    permissions = list(permissions)
    old_permissions = list(permissions)
    for name, srv in servers.items():
        mapped = _opencode_timeouts(srv.get("timeouts"))
        if srv.get("transport") == "http" or srv.get("url"):
            url_env = srv.get("url_env")
            url = f"{{env:{url_env}}}" if url_env else srv["url"]
            auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else ""
            headers = {"Authorization": f"Bearer {{env:{auth_env}}}"} if auth_env else {}
            entry: dict[str, Any] = {
                "type": "remote",
                "url": url,
            }
            if auth_env:
                entry["headers"] = headers
                entry["oauth"] = False
            elif srv.get("oauth"):
                # OpenCode handles the OAuth flow itself (discovery,
                # dynamic registration, tokens outside the config):
                # no headers, no client secrets in the config. An
                # explicit `oauth_client_id` (a public identifier, never
                # a secret) is forwarded for providers that do not
                # support dynamic client registration, e.g. Google
                # Workspace MCP.
                oauth_config: dict[str, Any] = {}
                client_id = srv.get("oauth_client_id")
                if isinstance(client_id, str) and client_id.strip():
                    oauth_config["client_id"] = client_id.strip()
                if oauth_config:
                    entry["oauth"] = oauth_config
            elif srv.get("oauth") is False and "oauth" in srv:
                entry["oauth"] = False
        else:
            cmd_list = [srv.get("command", "")] + list(srv.get("args", []))
            entry = {
                "type": "local",
                "command": cmd_list,
            }
            if srv.get("env"):
                entry["environment"] = srv["env"]
        if mapped:
            entry["timeout"] = mapped
        mcp_servers[name] = entry
        # V2 denies tools through permissions, including Code Mode tools.
        deny = srv.get("tools_deny")
        if isinstance(deny, list) and deny:
            for tool in deny:
                action = re.sub(r"[^A-Za-z0-9_-]", "_", f"{name}_{tool}")
                rule = {"action": action, "resource": "*", "effect": "deny"}
                if rule not in permissions:
                    permissions.append(rule)
                if tools_cfg.get(action) is False:
                    tools_cfg.pop(action)

    # This renderer owns MCP entries, not the user's native tool choices.
    # In particular, websearch remains available as a fallback when the
    # Firecrawl connector is unavailable. One exception, once: the
    # previous renderer forced `tools.websearch = false` together with
    # `websearch = "parallel"` on every machine it touched, so a bare
    # `false` paired with that exact marker is the old engine's
    # fingerprint, not a human choice. It is dropped so the documented
    # fallback lane works again; any other value (true, an object, a
    # named provider) is the user's and stays exactly as it is.
    if tools_cfg.get("websearch") is False and existing.get("websearch") == "parallel":
        tools_cfg.pop("websearch")
        existing.pop("websearch", None)
        drop_engine_websearch = True
    else:
        drop_engine_websearch = False
    if "tools" in existing or tools_cfg:
        existing["tools"] = tools_cfg
    if permissions:
        existing["permissions"] = permissions
    mcp_config["servers"] = mcp_servers
    existing["mcp"] = mcp_config
    if write:
        # JSONC-aware: preserves the existing file's comments instead of
        # overwriting it with plain JSON (which OpenCode wouldn't read).
        if cfg_file.suffix == ".jsonc" and raw_existing.strip():
            content = set_jsonc_top_level_value(raw_existing, "mcp", mcp_config)
            if "tools" in existing and tools_cfg != old_tools_cfg:
                content = set_jsonc_top_level_value(content, "tools", tools_cfg)
            if permissions and permissions != old_permissions:
                content = set_jsonc_top_level_value(content, "permissions", permissions)
            if drop_engine_websearch:
                content = remove_jsonc_top_level_value(content, "websearch")
        else:
            # File missing or empty: no comments to preserve.
            content = json.dumps(existing, indent=2) + "\n"
        renderer._backup_and_write(cfg_file, content)
    return True, t("OpenCode configuration updated")
