"""Where each MCP server lives, per CLI: mounted directly, behind the gateway, or nowhere.

This used to be decided in three places that did not agree. The renderer decided what each CLI mounts from
`tier`, `enabled`, `lazy` and `lazy_targets`; the gateway (`lazy-mcp`) decided what it serves from `lazy`
alone, ignoring `targets`, `enabled` and `lazy_targets`, and without knowing which CLI it was mounted in. So
a server routed directly to one CLI was also listed in that CLI's gateway, and a server restricted to two
CLIs was still served by the gateway to the other two. This module is the one rule; the renderer, the
gateway and `nexgen mcp plan` all ask it.

One declaration per server expresses the intent:

    exposure: eager    # mounted directly in every CLI listed in `targets`
    exposure: lazy     # behind the gateway in every CLI listed in `targets`

The older knobs (`tier`, `lazy`, `lazy_targets`) are still honoured when `exposure` is absent, so a manifest
that has not been migrated, and an older engine reading a migrated one, keep working. `targets` is always
the allow-list of CLIs and `enabled: false` always switches a server off.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CLIS: tuple[str, ...] = ("claude", "codex", "antigravity", "opencode", "openclaw")

#: The server that stands in front of the lazy ones. It must be mounted directly wherever it is needed.
GATEWAY = "lazy-mcp"

#: The environment variable through which a CLI's gateway learns which CLI it serves.
GATEWAY_CLI_ENV = "LAZY_MCP_CLI"

#: Same vocabulary as skills: `eager` is loaded up front (mounted directly), `lazy` on demand (gateway).
EXPOSURES: tuple[str, ...] = ("eager", "lazy")

DIRECT = "direct"
GATEWAY_KIND = "gateway"
ABSENT = "absent"


@dataclass(frozen=True)
class Placement:
    kind: str  # direct | gateway | absent
    why: str   # a short, stable reason, shown by `nexgen mcp plan`

    @property
    def mounted(self) -> bool:
        return self.kind != ABSENT


def _is_active_legacy(srv: dict[str, Any]) -> bool:
    return str(srv.get("tier", "")).strip().lower() == "core" or srv.get("enabled", False) is True


def _oauth_only(srv: dict[str, Any]) -> bool:
    """An HTTP server that asks for OAuth and gives the gateway no bearer token to send."""
    is_http = srv.get("transport") == "http" or bool(srv.get("url"))
    auth = srv.get("auth")
    has_bearer = isinstance(auth, dict) and bool(auth.get("env"))
    return is_http and bool(srv.get("oauth")) and not has_bearer


def _targets(srv: dict[str, Any], key: str) -> list[str]:
    """The CLIs a list-valued key allows. An absent key means every CLI; an empty one means none.

    `srv.get(key) or list(CLIS)` read `targets: []` as "every CLI", so a server the
    manifest keeps off every machine was mounted on all of them.
    """
    raw = srv.get(key)
    return list(CLIS) if raw is None else list(raw)


def _is_gateway(srv: dict[str, Any], name: str | None = None) -> bool:
    """Whether srv is the gateway server (lazy-mcp)."""
    if name == GATEWAY or srv.get("name") == GATEWAY:
        return True
    cmd = str(srv.get("command") or "")
    args = [str(a) for a in srv.get("args") or []]
    return "lazy-mcp" in cmd or any("lazy-mcp" in a for a in args)


def _place_openclaw(srv: dict[str, Any], name: str | None = None) -> Placement:
    """Every enabled server targeting OpenClaw is mounted directly; lazy-mcp is never mounted."""
    if _is_gateway(srv, name):
        return Placement(ABSENT, "openclaw: gateway not mounted")
    exposure = srv.get("exposure")
    if exposure == "eager":
        return Placement(DIRECT, "exposure: eager")
    if exposure == "lazy":
        return Placement(DIRECT, "openclaw: direct")
    if exposure is not None:
        return Placement(ABSENT, f"unknown exposure {exposure!r}")
    if srv.get("lazy"):
        return Placement(DIRECT, "openclaw: direct")
    if _is_active_legacy(srv):
        return Placement(DIRECT, "legacy: core" if str(srv.get("tier", "")).strip().lower() == "core" else "legacy: enabled")
    return Placement(ABSENT, "inert (not core, not enabled)")


def _place_impl(srv: dict[str, Any], cli: str, name: str | None = None) -> Placement:
    """Where `srv` lives for `cli`. Environment gates (`require_env`) are the caller's: they depend on the shell."""
    targets = _targets(srv, "targets")
    if cli not in targets:
        return Placement(ABSENT, "not in targets")
    if srv.get("enabled") is False:
        return Placement(ABSENT, "disabled")
    if cli == "openclaw":
        return _place_openclaw(srv, name)
    exposure = srv.get("exposure")
    if exposure is not None:
        if exposure == "eager":
            return Placement(DIRECT, "exposure: eager")
        if exposure == "lazy":
            return Placement(GATEWAY_KIND, "exposure: lazy")
        return Placement(ABSENT, f"unknown exposure {exposure!r}")
    # Legacy knobs, for a manifest that has not declared `exposure`.
    if srv.get("lazy"):
        lazy_targets = _targets(srv, "lazy_targets")
        if cli in lazy_targets:
            return Placement(GATEWAY_KIND, "legacy: lazy")
    if _is_active_legacy(srv):
        return Placement(DIRECT, "legacy: core" if str(srv.get("tier", "")).strip().lower() == "core" else "legacy: enabled")
    return Placement(ABSENT, "inert (not core, not enabled)")


def place(srv: dict[str, Any], cli: str) -> Placement:
    """Where `srv` lives for `cli`. Environment gates (`require_env`) are the caller's: they depend on the shell."""
    return _place_impl(srv, cli)


def _plan_impl(servers: dict[str, dict[str, Any]], clis: tuple[str, ...] = CLIS) -> dict[str, dict[str, Placement]]:
    """name -> cli -> Placement, for every server."""
    return {name: {cli: _place_impl(srv, cli, name=name) for cli in clis} for name, srv in servers.items() if isinstance(srv, dict)}


def plan(servers: dict[str, dict[str, Any]], clis: tuple[str, ...] = CLIS) -> dict[str, dict[str, Placement]]:
    """name -> cli -> Placement, for every server."""
    return _plan_impl(servers, clis)


def _problems_impl(servers: dict[str, dict[str, Any]], clis: tuple[str, ...] = CLIS) -> list[str]:
    """What makes the plan incoherent, in plain words: a CLI that would be told to use a gateway it does not have."""
    found: list[str] = []
    table = _plan_impl(servers, clis)
    gateway = table.get(GATEWAY)
    for cli in clis:
        routed = sorted(name for name, row in table.items() if name != GATEWAY and row[cli].kind == GATEWAY_KIND)
        if routed and (gateway is None or gateway[cli].kind != DIRECT):
            found.append(f"{cli}: {', '.join(routed)} are routed to the gateway ({GATEWAY}), which is not mounted there")
    for name, row in table.items():
        for cli, placement in row.items():
            if placement.why.startswith("unknown exposure"):
                found.append(f"{name}: {placement.why} (expected one of {', '.join(EXPOSURES)}); not mounted in {cli}")
                break
    for name, srv in servers.items():
        if not isinstance(srv, dict) or not _oauth_only(srv):
            continue
        behind = [cli for cli in clis if _place_impl(srv, cli, name=name).kind == GATEWAY_KIND]
        if behind:
            found.append(
                f"{name}: a server that authenticates with OAuth cannot do it behind the gateway (which can only send a bearer "
                f"token read from an environment variable), so every call fails with 401 while its tool list still looks healthy "
                f"({', '.join(behind)}); give it `auth.env`, or mount it where the CLI handles OAuth itself (exposure: eager)"
            )
    gateway_srv = servers.get(GATEWAY)
    if isinstance(gateway_srv, dict) and gateway_srv.get("exposure") == "lazy":
        found.append(f"{GATEWAY}: the gateway cannot be behind itself; use exposure: eager")
    return found


def problems(servers: dict[str, dict[str, Any]], clis: tuple[str, ...] = CLIS) -> list[str]:
    """What makes the plan incoherent, in plain words: a CLI that would be told to use a gateway it does not have."""
    return _problems_impl(servers, clis)


def _gateway_servers_for_impl(servers: dict[str, dict[str, Any]], cli: str) -> set[str]:
    """The servers the gateway mounted in `cli` must serve: exactly those placed behind it for that CLI."""
    return {name for name, srv in servers.items() if isinstance(srv, dict) and name != GATEWAY and _place_impl(srv, cli, name=name).kind == GATEWAY_KIND}


def gateway_servers_for(servers: dict[str, dict[str, Any]], cli: str) -> set[str]:
    """The servers the gateway mounted in `cli` must serve: exactly those placed behind it for that CLI."""
    return _gateway_servers_for_impl(servers, cli)
