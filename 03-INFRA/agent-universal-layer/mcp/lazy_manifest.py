#!/usr/bin/env python3
"""lazy_manifest.py — which servers this gateway serves, and each entry ready to spawn.

The gateway cannot index or forward anything before it answers one question: for the CLI it is
mounted in, which manifest entries route through it, and what each entry looks like once the
platform override, the inline templates and the declared credentials are applied. This module
answers exactly that. It reads and prepares entries; it never starts a process or calls a tool.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

import lazy_env

#: A value that is a plain reference to one variable, and nothing else: only that spelling may reach a credential.
PURE_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-)?\}$")


def with_trials(data: dict[str, Any]) -> dict[str, Any]:
    """The manifest plus the servers on trial on this machine (never synced, they expire by themselves)."""
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        from nexgen_core import mcp_trials

        servers = mcp_trials.overlay(dict(data.get("servers") or {}))
    except Exception as exc:  # noqa: BLE001 - a broken trial file must never take the gateway down
        print(f"[lazy-mcp] trials ignored ({type(exc).__name__})", file=sys.stderr)
        return data
    return {**data, "servers": servers}


def placement_module():
    """The engine's one placement rule (nexgen_core.mcp_placement), or None when the package is unreachable."""
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        from nexgen_core import mcp_placement
    except ImportError as exc:
        print(f"[lazy-mcp] placement rule unavailable ({exc}); serving every lazy server", file=sys.stderr)
        return None
    return mcp_placement


def served_cli() -> str:
    """The CLI this gateway is mounted in, as the renderer told it (LAZY_MCP_CLI), or '' when it was not told."""
    return os.environ.get("LAZY_MCP_CLI", "").strip().lower()


def served_by_gateway(srv: dict[str, Any], cli: str, placement) -> bool:
    """Whether this gateway, mounted in `cli`, serves `srv`: the placement rule's one answer.

    It used to serve every `lazy: true` server to every CLI, so a server mounted directly in a CLI was also in
    that CLI's gateway, and `targets` and `enabled` were ignored. It now asks `mcp_placement.gateway_serves`,
    which routes behind it for the other CLIs and mirrors what OpenClaw mounts directly, where the gateway is
    a `claude-cli` agent's only road to a connector. A gateway that was never told its CLI (a config written
    before this) keeps the old behaviour until the next guard cycle rewrites it.
    """
    if cli and placement is not None:
        return placement.gateway_serves(srv, cli)
    return bool(srv.get("lazy")) or srv.get("exposure") == "lazy"


def missing_credentials(srv: dict[str, Any], credentials: lazy_env.Credentials) -> list[str]:
    """The variables this entry needs and this session does not have.

    What counts: `require_env` (the manifest's own gate), a bearer token it declares (`auth.env`), and any
    `env` entry that is a plain reference to a secret-looking variable. A missing one used to make a server
    vanish from the index, or fail with a bare 401, with nothing saying why.
    """
    needed: list[str] = []
    if srv.get("require_env"):
        needed.append(str(srv["require_env"]))
    auth = srv.get("auth")
    if isinstance(auth, dict) and auth.get("env"):
        needed.append(str(auth["env"]))
    if isinstance(srv.get("env"), dict):
        for value in srv["env"].values():
            match = PURE_REFERENCE.match(str(value).strip())
            if match and lazy_env.is_secret_name(match.group(1)):
                needed.append(match.group(1))
    seen: list[str] = []
    for name in needed:
        if name not in seen and not credentials.declared(name):
            seen.append(name)
    return seen


def expand_env_value(value: str, ctx: dict[str, str], credentials: lazy_env.Credentials,
                     expand_templates: Callable[[str], str]) -> str:
    """An `env` value of a server entry. A plain `${NAME}` is the credential the entry says it needs: it is looked
    up everywhere a credential can live. Anything composed (`prefix-${NAME}`) only sees the process and the session."""
    text = expand_templates(value)
    match = PURE_REFERENCE.match(text.strip())
    if match and match.group(1) not in ctx:
        return credentials.declared(match.group(1))
    return credentials.expand(text, ctx)


def prepare_entry(name: str, srv: dict[str, Any], ctx: dict[str, str], credentials: lazy_env.Credentials,
                  expand_templates: Callable[[str], str]) -> dict[str, Any] | None:
    """A manifest entry ready to spawn: platform override applied, placeholders and templates expanded.

    Carries `_unavailable` (why it cannot work in this session) instead of being dropped, so the index can say
    "needs VERCEL_API_TOKEN" rather than leave the server out in silence. None when the entry is unusable.
    """
    entry = {k: v for k, v in srv.items()}
    if sys.platform == "win32" and "windows" in entry:
        win_override = entry.pop("windows")
        if isinstance(win_override, dict):
            entry.update(win_override)
    entry.setdefault("readonly", False)
    entry.setdefault("readonly_tools", [])
    missing = missing_credentials(entry, credentials)
    if missing:
        entry["_unavailable"] = "needs " + ", ".join(missing) + " in this session's environment"
    # DEPS_WORKSPACE is a SELF-PRESERVING token here: its real value is
    # known only at spawn time, after provisioning. Expanding it now to
    # anything else (including '') would destroy the token before the
    # spawn can substitute the provisioned workspace.
    load_ctx = dict(ctx)
    load_ctx["DEPS_WORKSPACE"] = "${DEPS_WORKSPACE}"
    try:
        if entry.get("command"):
            entry["command"] = credentials.expand(expand_templates(str(entry["command"])), load_ctx)
        if isinstance(entry.get("args"), list):
            entry["args"] = [credentials.expand(expand_templates(str(a)), load_ctx) for a in entry["args"]]
        if entry.get("url"):
            entry["url"] = credentials.expand(expand_templates(str(entry["url"])), load_ctx)
        if isinstance(entry.get("env"), dict):
            entry["env"] = {k: expand_env_value(str(v), load_ctx, credentials, expand_templates)
                            for k, v in entry["env"].items()}
    except Exception as exc:
        # Fail closed per entry: this server is withdrawn from the
        # index with the reason on stderr, instead of spawning literal
        # {{ }} text or taking the whole index down with it.
        print(f"[lazy-mcp] server '{name}' withdrawn: inline template error ({exc})", file=sys.stderr)
        return None
    return entry


def lazy_servers(data: dict[str, Any], ctx: dict[str, str], credentials: lazy_env.Credentials,
                 expand_templates: Callable[[str], str]) -> dict[str, dict[str, Any]]:
    """The servers behind this gateway for the CLI it serves (placeholders expanded; `_unavailable` says why one
    cannot work)."""
    cli = served_cli()
    placement = placement_module() if cli else None
    out: dict[str, dict[str, Any]] = {}
    for name, srv in (data.get("servers") or {}).items():
        if not isinstance(srv, dict) or name == "lazy-mcp" or not served_by_gateway(srv, cli, placement):
            continue
        entry = prepare_entry(name, srv, ctx, credentials, expand_templates)
        if entry is not None:
            out[name] = entry
    return out


def served_entry(name: str, data: dict[str, Any], ctx: dict[str, str], credentials: lazy_env.Credentials,
                 expand_templates: Callable[[str], str]) -> dict[str, Any] | None:
    """One server's entry regardless of where the plan places it (the trimming shim serves a server mounted directly)."""
    srv = (data.get("servers") or {}).get(name)
    return prepare_entry(name, srv, ctx, credentials, expand_templates) if isinstance(srv, dict) else None
