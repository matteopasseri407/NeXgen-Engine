"""Reachability checks for 'core'-tier MCP connectors.

Ported from the "MCP connectors — reachability" section of the release's
bash doctor, but made generic: instead of probing hardcoded ports by
connector name, it derives the endpoint to probe directly from the resolved
manifest. Uses only stdlib (socket/urllib) with a short, per-server timeout:
one slow connector must never hold up the whole doctor.
"""
from __future__ import annotations

import re
import socket
from pathlib import Path
from urllib.parse import urlparse

from nexgen_core.i18n import t
from nexgen_core.renderer import McpRenderer
from nexgen_core.report import CheckOutcome, Severity

#: Per-server timeout for the reachability probe (seconds). Deliberately
#: short: the probe is per-connector, not cumulative across the whole
#: manifest, and a slow connector has already held up the entire doctor
#: before.
PROBE_TIMEOUT_SECONDS = 2.5

_URL_RE = re.compile(r"https?://[^\s\"']+")


def _is_loopback(host: str) -> bool:
    """Is this address on this machine?"""
    import ipaddress

    if host in ("localhost", "ip6-localhost", "ip6-loopback"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _extract_probe_target(entry: dict) -> str | None:
    """Derive a URL to probe from a resolved MCP connector.

    An http connector declares it directly in `url`. A stdio connector can
    still depend on a local backend reachable over HTTP, declared in `env`
    (e.g. FIRECRAWL_API_URL) or in `args` (e.g. Playwright's CDP endpoint):
    we look for it there instead of assuming which connector exposes it and
    how.
    """
    if entry.get("url"):
        return str(entry["url"])

    candidates: list[str] = []
    env = entry.get("env")
    if isinstance(env, dict):
        candidates.extend(str(v) for v in env.values())
    args = entry.get("args")
    if isinstance(args, list):
        candidates.extend(str(a) for a in args)

    for candidate in candidates:
        match = _URL_RE.search(candidate)
        if match:
            return match.group(0)
    return None


def _probe_tcp(url: str, timeout: float) -> tuple[Severity, str]:
    """Probes TCP reachability only: no assumption about the connector's
    application protocol (health path, HTTP verb, expected body).
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return Severity.UNDETERMINED, f"could not determine the host to check in '{url}'"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return Severity.OK, f"{host}:{port} responds"
    except ConnectionRefusedError:
        # Refused on loopback means the local service simply is not running,
        # and whether it should be is the user's choice, not the engine's. A
        # fresh install that answered "no services" was getting a red failure
        # for a connector it had just declined — a correct install that looks
        # broken teaches people to ignore the report. Refused by a host
        # somewhere else is different: something declared a server, and the
        # server is not answering.
        if _is_loopback(host):
            return Severity.UNDETERMINED, f"{host}:{port} has nothing listening right now"
        return Severity.BROKEN, f"{host}:{port} refuses the connection (no service listening)"
    except TimeoutError:
        return Severity.UNDETERMINED, f"{host}:{port} did not respond within {timeout}s"
    except OSError as exc:
        return Severity.UNDETERMINED, f"{host}:{port} unreachable ({exc})"


def check_mcp_reachability(
    vault_data: Path, home: Path, timeout: float = PROBE_TIMEOUT_SECONDS
) -> list[CheckOutcome]:
    """Check that every `tier: core` MCP connector whose precondition is
    met actually responds.

    A connector whose `require_env` is not satisfied is never considered:
    it's not a failure, it's a configuration the user hasn't chosen to
    enable on this machine.
    """
    manifest_path = vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    if not manifest_path.is_file():
        return []

    renderer = McpRenderer(vault_data=vault_data, home=home)
    resolved: dict[str, dict] = {}
    for cli in ("claude", "codex", "antigravity", "opencode"):
        try:
            for name, entry in renderer.load_resolved_servers(cli).items():
                resolved.setdefault(name, entry)
        except (OSError, ValueError):
            continue

    outcomes: list[CheckOutcome] = []
    for name, entry in sorted(resolved.items()):
        if entry.get("tier") != "core":
            continue
        target = _extract_probe_target(entry)
        if not target:
            continue

        severity, detail = _probe_tcp(target, timeout)
        id_ = f"mcp.reachable.{name}"
        if severity == Severity.OK:
            outcomes.append(CheckOutcome(
                id=id_, severity=Severity.OK,
                message=t("MCP connector '{name}' reachable", name=name),
            ))
        elif severity == Severity.BROKEN:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.BROKEN,
                message=t("MCP connector '{name}' is not responding ({detail}).", name=name, detail=detail),
                action=t("Check that the service behind '{name}' is running, then rerun the doctor.", name=name),
            ))
        else:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.UNDETERMINED,
                message=t("Could not check the reachability of '{name}' ({detail}).", name=name, detail=detail),
                action=t(
                    "Nothing to do if you did not mean to run it. To run the connectors "
                    "on this machine: 'nexgen stack up'."
                ),
            ))
    return outcomes
