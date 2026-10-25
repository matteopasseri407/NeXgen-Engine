"""OpenClaw MCP configuration checks: no plaintext credentials or secret URLs.

OpenClaw validates `mcp.servers.*.url` before substituting environment variables,
so an operator or legacy renderer might inadvertently write the resolved secret URL
(e.g. the vault's private token or hexadecimal path) into `openclaw.json`.
The doctor detects such cleartext exposures and flags them as FAIL without ever
printing the secret value.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.mcp_render.openclaw import ENGINE_RESOLVED_VARS
from nexgen_core.report import CheckOutcome, Severity

_REFERENCE_NAME = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")


def manifest_secret_vars(manifest_path: Path) -> set[str]:
    """Variable names declared in the MCP manifest that hold secrets or secret URLs."""
    if not manifest_path.is_file():
        return set()
    try:
        from nexgen_core.config import load_mcp_manifest

        data = load_mcp_manifest(manifest_path)
    except (OSError, ValueError):  # nexgen: allow reason="a broken manifest is reported by its own check; doctor returns no secret variables"
        return set()
    servers = data.get("servers") if isinstance(data.get("servers"), dict) else {}
    secret_vars: set[str] = set()
    for srv in servers.values():
        if not isinstance(srv, dict):
            continue
        url_env = srv.get("url_env")
        if isinstance(url_env, str) and url_env.strip():
            secret_vars.add(url_env.strip())
        raw_url = str(srv.get("url") or "")
        if "${" in raw_url:
            for ref in _REFERENCE_NAME.findall(raw_url):
                if ref not in ENGINE_RESOLVED_VARS:
                    secret_vars.add(ref)
        auth = srv.get("auth") if isinstance(srv.get("auth"), dict) else {}
        auth_env = auth.get("env")
        if isinstance(auth_env, str) and auth_env.strip():
            secret_vars.add(auth_env.strip())
        raw_env = srv.get("env") if isinstance(srv.get("env"), dict) else {}
        for val in raw_env.values():
            if isinstance(val, str) and "${" in val:
                for ref in _REFERENCE_NAME.findall(val):
                    if ref not in ENGINE_RESOLVED_VARS:
                        secret_vars.add(ref)
        win = srv.get("windows")
        if isinstance(win, dict):
            win_url_env = win.get("url_env")
            if isinstance(win_url_env, str) and win_url_env.strip():
                secret_vars.add(win_url_env.strip())
            win_url = str(win.get("url") or "")
            if "${" in win_url:
                for ref in _REFERENCE_NAME.findall(win_url):
                    if ref not in ENGINE_RESOLVED_VARS:
                        secret_vars.add(ref)
    return secret_vars


def check_mcp_secret_url(
    config: dict[str, Any],
    manifest_path: Path,
    env: Mapping[str, str],
) -> CheckOutcome | None:
    """FAIL if mcp.servers.*.url contains a literal URL matching a secret environment variable."""
    secret_vars = manifest_secret_vars(manifest_path)
    if not secret_vars:
        return None
    secret_values = {
        env[var].strip().rstrip("/")
        for var in secret_vars
        if var in env and len(env[var].strip()) >= 4
    }
    if not secret_values:
        return None

    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    servers = mcp.get("servers") if isinstance(mcp.get("servers"), dict) else {}
    leaked: list[str] = []
    for name, entry in sorted(servers.items()):
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url or "${" in url:
            continue
        if url.rstrip("/") in secret_values:
            leaked.append(name)

    if leaked:
        return CheckOutcome(
            id="openclaw.mcp.secret_url",
            severity=Severity.BROKEN,
            message=t(
                "MCP server '{servers}' has a literal URL matching a secret declared in the manifest; credentials must not be stored in openclaw.json.",
                servers=", ".join(leaked),
            ),
            action=t("Run 'nexgen sync' or 'nexgen mcp render' to rewrite the connector through the bridge."),
        )
    return CheckOutcome(
        id="openclaw.mcp.secret_url",
        severity=Severity.OK,
        message=t("No literal secret URLs in OpenClaw MCP configuration."),
    )
