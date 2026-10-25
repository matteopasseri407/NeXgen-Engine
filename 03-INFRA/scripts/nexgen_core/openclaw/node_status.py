"""OpenClaw node status parsing and gateway endpoint extraction.

One job: parse structured JSON output from documented 'openclaw node status --json'
across supported OS service managers (Windows Scheduled Tasks, Linux systemd, macOS
LaunchAgents), resolving service load state, active runtime status, and gateway origin.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ParsedNodeStatus:
    """Parsed status payload from 'openclaw node status --json'."""

    is_node: bool
    is_running: bool
    gateway_url: str | None
    gateway_origin: str | None
    raw_status: str | None = None


def normalize_gateway_origin(target: str) -> str:
    """Extracts host or host:port from a join URL, endpoint, or raw host address."""
    clean = target.strip()
    if not clean:
        return ""
    scheme = ""
    if "://" in clean:
        parsed = urlsplit(clean)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
    else:
        netloc = clean.split("/")[0].lower()

    if netloc.endswith(":443") and scheme in ("https", "wss", ""):
        netloc = netloc[:-4]
    elif netloc.endswith(":80") and scheme in ("http", "ws", ""):
        netloc = netloc[:-3]
    return netloc


def parse_node_service_command(args: list[str]) -> tuple[bool, str | None]:
    """Inspects programArguments to check if it represents 'node run' and extracts gateway URL."""
    if not args:
        return False, None

    is_node_run = False
    for i in range(len(args) - 1):
        if args[i] == "node" and args[i + 1] == "run":
            is_node_run = True
            break
    if not is_node_run:
        is_node_run = "node" in args and "run" in args

    if not is_node_run:
        return False, None

    host = "127.0.0.1"
    port = None
    tls = False

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
            continue
        elif arg.startswith("--host="):
            host = arg.split("=", 1)[1]
        elif arg == "--port" and i + 1 < len(args):
            port = args[i + 1]
            i += 2
            continue
        elif arg.startswith("--port="):
            port = arg.split("=", 1)[1]
        elif arg == "--tls":
            tls = True
        elif arg == "--no-tls":
            tls = False
        i += 1

    scheme = "wss" if tls else "ws"
    if port and not (tls and str(port) == "443") and not (not tls and str(port) == "80"):
        gw_url = f"{scheme}://{host}:{port}"
    else:
        gw_url = f"{scheme}://{host}"
    return True, gw_url


def parse_node_status_payload(data: Any) -> ParsedNodeStatus:
    """Parses output from 'openclaw node status --json' (real service shape and legacy)."""
    if not isinstance(data, dict):
        return ParsedNodeStatus(is_node=False, is_running=False, gateway_url=None, gateway_origin=None)

    service = data.get("service")
    if isinstance(service, dict):
        loaded = bool(service.get("loaded"))
        cmd = service.get("command") or {}
        args = cmd.get("programArguments") if isinstance(cmd, dict) else []
        is_node_cmd, gw_url = parse_node_service_command(args or [])
        is_node = loaded and is_node_cmd

        runtime = service.get("runtime") or {}
        status_str = ""
        if isinstance(runtime, dict):
            status_str = str(runtime.get("status") or "").strip().lower()
        is_running = is_node and (status_str == "running")

        origin = normalize_gateway_origin(gw_url) if gw_url else None
        return ParsedNodeStatus(
            is_node=is_node,
            is_running=is_running,
            gateway_url=gw_url,
            gateway_origin=origin,
            raw_status=status_str or ("running" if is_running else "stopped"),
        )

    # Fallback for direct / legacy payloads
    status_str = str(data.get("status") or "").strip().lower()
    conn = data.get("connected")
    is_running = bool(conn) if conn is not None else (status_str in ("connected", "running", "online"))
    gw = data.get("gateway") or data.get("gatewayUrl") or data.get("endpoint")
    is_node = bool(data.get("is_node") or gw or is_running or ("status" in data))
    origin = normalize_gateway_origin(gw) if gw else None
    return ParsedNodeStatus(
        is_node=is_node,
        is_running=is_running,
        gateway_url=gw,
        gateway_origin=origin,
        raw_status=status_str or ("running" if is_running else "stopped"),
    )
