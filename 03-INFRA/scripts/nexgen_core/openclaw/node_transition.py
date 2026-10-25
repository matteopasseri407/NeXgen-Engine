"""Inspection, planning, and safe service teardown for node role transition.

One job: manage the transition of an existing engine machine to an OpenClaw node,
inspecting pre-existing services and gateway pairings, generating a non-destructive
execution plan, safely decommissioning local gateway services without touching state,
and formatting the final one-screen operational summary.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.companion import probe as probe_companion
from nexgen_core.openclaw.node_cli import READ_TIMEOUT_S, SERVICE_TIMEOUT_S, Run, run_command
from nexgen_core.openclaw.node_status import (
    normalize_gateway_origin,
    parse_node_status_payload,
)
from nexgen_core.paths import openclaw_config, resolve_home


@dataclass(frozen=True)
class EngineVersionInfo:
    """Installed engine version and target version if known."""

    installed_version: str
    target_version: str | None = None


@dataclass(frozen=True)
class LocalGatewayInfo:
    """Details about any pre-existing local OpenClaw gateway on this machine."""

    present: bool
    mechanism: str | None
    service_active: bool
    scheduled_task_present: bool
    startup_vbs_present: bool
    companion_present: bool
    companion_detail: str | None
    state_dir: Path
    state_dir_exists: bool


@dataclass(frozen=True)
class ExistingNodeInfo:
    """Whether this machine is already configured or connected as a node."""

    is_node: bool
    is_running: bool
    gateway_url: str | None
    gateway_origin: str | None
    node_name: str | None = None
    service_status: str | None = None
    connected: bool = False
    readable: bool = True
    unreadable_reason: str | None = None


@dataclass(frozen=True)
class TransitionInspection:
    """Full snapshot of this machine prior to transitioning to a node."""

    engine: EngineVersionInfo
    gateway: LocalGatewayInfo
    node: ExistingNodeInfo


def detect_engine_versions(
    root: Path,
    state_dir: Path | None = None,
) -> EngineVersionInfo:
    """Reads current engine version and looks up target release if possible."""
    version_file = root / "VERSION"
    installed = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else "unknown"
    target = None
    try:
        from nexgen_core import update_channel

        st_dir = state_dir or (resolve_home() / ".local" / "state" / "nexgen")
        plan = update_channel.plan_target(root, st_dir, channel=None)
        target = plan.version
    except Exception:  # nexgen: allow reason="offline or uninitialized git state defaults to installed version"
        target = installed
    return EngineVersionInfo(installed_version=installed, target_version=target)


def _detect_win_artifacts(
    plat: str,
    resolved_startup: Path,
    state_dir: Path,
    runner: Run | None,
    run_fn: Run,
) -> tuple[bool, bool]:
    """Detects Windows startup VBS file and scheduled task."""
    if plat != "win32":
        return False, False
    vbs = resolved_startup / "OpenClaw Gateway.vbs"
    startup_vbs_present = vbs.is_file()
    scheduled_task_present = False
    if startup_vbs_present or state_dir.is_dir() or runner is not None:
        sch_res = run_fn(["schtasks.exe", "/Query", "/TN", "OpenClaw Gateway"], READ_TIMEOUT_S)
        scheduled_task_present = sch_res.returncode == 0
    return scheduled_task_present, startup_vbs_present


def _detect_companion_safe(
    plat: str,
    resolved_home: Path,
    runner: Any,
    state_dir: Path,
    resolved_appdata: Path,
) -> tuple[bool, str | None]:
    """Safely probes for Companion app presence on Windows without blocking."""
    if not plat.startswith("win"):
        return False, None
    if runner is None and not (state_dir.is_dir() or resolved_appdata.is_dir()):
        return False, None
    try:
        comp = probe_companion(resolved_home, os.environ, plat, wsl_distributions=list)
        detail = ", ".join(comp.signals) if comp.signals else None
        return comp.present, detail
    except Exception:  # nexgen: allow reason="companion probe failure on unconfigured desktop safely treated as absent"
        return False, None


def detect_local_gateway(
    home: Path | None = None,
    platform: str | None = None,
    runner: Run | None = None,
    appdata: Path | None = None,
    startup_dir: Path | None = None,
    systemd_user_dir: Path | None = None,
) -> LocalGatewayInfo:
    """Detects local gateway services, scheduled tasks, and companion app."""
    run_fn = runner or run_command
    plat = platform if platform is not None else sys.platform
    resolved_home = resolve_home(home)
    state_dir = openclaw_config(resolved_home).parent
    resolved_appdata = appdata or (resolved_home / "AppData" / "Roaming")
    resolved_startup = startup_dir or (
        resolved_appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    )
    resolved_systemd = systemd_user_dir or (resolved_home / ".config" / "systemd" / "user")

    sch_present, vbs_present = _detect_win_artifacts(plat, resolved_startup, state_dir, runner, run_fn)
    systemd_present = (resolved_systemd / "openclaw-gateway.service").is_file() if plat != "win32" else False
    comp_present, comp_detail = _detect_companion_safe(plat, resolved_home, runner, state_dir, resolved_appdata)

    mechanism = "schtasks" if sch_present else ("vbs" if vbs_present else ("systemd" if systemd_present else None))
    is_present = bool(sch_present or vbs_present or systemd_present)

    if not is_present and state_dir.is_dir() and (state_dir / "openclaw.json").is_file():
        stat_res = run_fn(["openclaw", "gateway", "status", "--json"], READ_TIMEOUT_S)
        if stat_res.returncode == 0 and "installed" in stat_res.stdout.lower():
            is_present = True
            mechanism = "npm"

    return LocalGatewayInfo(
        present=is_present,
        mechanism=mechanism,
        service_active=is_present,
        scheduled_task_present=sch_present,
        startup_vbs_present=vbs_present,
        companion_present=comp_present,
        companion_detail=comp_detail,
        state_dir=state_dir,
        state_dir_exists=state_dir.is_dir(),
    )


def _query_raw_node_status(
    run_fn: Run,
) -> tuple[Any | None, str | None]:
    """Runs openclaw node status --json and returns parsed JSON or failure reason."""
    res = run_fn(["openclaw", "node", "status", "--json"], READ_TIMEOUT_S)
    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        reason = err or f"codice d'uscita {res.returncode}"
        if "not found" in reason.lower() or res.returncode == 127:
            reason = "eseguibile openclaw non trovato"
        return None, reason

    if not res.stdout.strip():
        return None, "nessun output da openclaw node status"

    try:
        return json.loads(res.stdout), None
    except json.JSONDecodeError:
        return None, "output JSON non valido da openclaw node status"


def _fallback_node_json(
    st_dir: Path,
    gw_url: str | None,
    node_name: str | None,
) -> tuple[bool, str | None, str | None]:
    """Reads ~/.openclaw/node.json if gateway_url was not returned by status."""
    if not gw_url and st_dir.is_dir():
        node_json = st_dir / "node.json"
        if node_json.is_file():
            try:
                ndata = json.loads(node_json.read_text(encoding="utf-8"))
                return True, ndata.get("gateway") or ndata.get("host"), node_name or ndata.get("displayName")
            except Exception:  # nexgen: allow reason="corrupted or unreadable node.json treated as missing node info"
                pass
    return bool(gw_url), gw_url, node_name


def detect_existing_node(
    runner: Run | None = None,
    home: Path | None = None,
    state_dir: Path | None = None,
) -> ExistingNodeInfo:
    """Checks whether this machine is already configured or connected as a node."""
    run_fn = runner or run_command
    resolved_home = resolve_home(home)
    st_dir = state_dir or openclaw_config(resolved_home).parent

    data, unreadable_reason = _query_raw_node_status(run_fn)
    if unreadable_reason is not None:
        return ExistingNodeInfo(
            is_node=False,
            is_running=False,
            gateway_url=None,
            gateway_origin=None,
            readable=False,
            unreadable_reason=unreadable_reason,
        )

    parsed = parse_node_status_payload(data)
    node_name = data.get("displayName") or data.get("name") if isinstance(data, dict) else None
    is_node, gw_url, node_name = _fallback_node_json(st_dir, parsed.gateway_url, node_name)
    origin = normalize_gateway_origin(gw_url) if gw_url else None

    return ExistingNodeInfo(
        is_node=parsed.is_node or is_node,
        is_running=parsed.is_running,
        gateway_url=gw_url,
        gateway_origin=origin,
        node_name=node_name,
        service_status=parsed.raw_status,
        connected=parsed.is_running,
        readable=True,
        unreadable_reason=None,
    )


def inspect_node_transition(
    root: Path,
    home: Path | None = None,
    platform: str | None = None,
    runner: Run | None = None,
    appdata: Path | None = None,
    startup_dir: Path | None = None,
    systemd_user_dir: Path | None = None,
    out: Callable[[str], None] = print,
) -> TransitionInspection:
    """Collects complete machine facts before transitioning to node role."""
    engine_info = detect_engine_versions(root)
    gateway_info = detect_local_gateway(
        home=home,
        platform=platform,
        runner=runner,
        appdata=appdata,
        startup_dir=startup_dir,
        systemd_user_dir=systemd_user_dir,
    )
    # A cold OpenClaw takes seconds to answer; say so before the pause, not after it.
    out(t("Leggo lo stato del nodo: a freddo OpenClaw può impiegare qualche secondo."))
    node_info = detect_existing_node(runner=runner, home=home, state_dir=gateway_info.state_dir)
    return TransitionInspection(engine=engine_info, gateway=gateway_info, node=node_info)


def plan_node_transition(inspection: TransitionInspection) -> list[str]:
    """Generates human-readable sentences for dry-run inspection (--check)."""
    lines: list[str] = [t("Piano di passaggio a nodo per questo PC:")]
    inst = inspection.engine.installed_version
    target = inspection.engine.target_version
    if target and target != inst:
        lines.append(t("- Motore NeXgen: versione installata {inst}, versione in arrivo {target}.",
                       inst=inst, target=target))
    else:
        lines.append(t("- Motore NeXgen: versione installata {inst} (già aggiornato).", inst=inst))

    if inspection.gateway.present:
        mech = inspection.gateway.mechanism or "locale"
        lines.append(t("- Gateway OpenClaw locale: presente (servizio {mech}). Verrà fermato e disattivato all'avvio.",
                       mech=mech))
    else:
        lines.append(t("- Gateway OpenClaw locale: non presente su questa macchina."))

    if inspection.gateway.companion_present:
        lines.append(t("- App OpenClaw Companion: rilevata (non verrà toccata nei suoi file)."))

    if not inspection.node.readable:
        lines.append(t("- Stato del nodo non leggibile: {reason}.", reason=inspection.node.unreadable_reason))
    elif inspection.node.is_node and inspection.node.gateway_url:
        if not inspection.node.is_running:
            lines.append(t("- Stato nodo: già nodo del gateway {gw}, servizio fermo.", gw=inspection.node.gateway_url))
        else:
            lines.append(t("- Stato nodo: già nodo del gateway {gw}.", gw=inspection.node.gateway_url))
    else:
        lines.append(t("- Stato nodo: non ancora collegato come nodo a un gateway."))

    if inspection.gateway.present:
        lines.append(t("- Cosa verrà fermato: il gateway OpenClaw locale (il servizio verrà disinstallato, nessuna cartella cancellata)."))
    else:
        lines.append(t("- Cosa verrà fermato: nessun servizio gateway locale attivo."))

    lines.append(t("- Cosa resta com'è: la cartella di stato (~/.openclaw con sessioni, memoria e configurazione), gli agenti, le skill e i connettori MCP configurati."))
    return lines


def stop_local_gateway_safe(
    home: Path | None = None,
    platform: str | None = None,
    runner: Run | None = None,
    appdata: Path | None = None,
    startup_dir: Path | None = None,
    systemd_user_dir: Path | None = None,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Gracefully stops and decommissions local gateway without touching state directory."""
    run_fn = runner or run_command
    plat = platform if platform is not None else sys.platform
    resolved_home = resolve_home(home)

    out(t("Disattivazione gateway OpenClaw locale..."))
    run_fn(["openclaw", "gateway", "stop"], SERVICE_TIMEOUT_S)
    run_fn(["openclaw", "gateway", "uninstall"], SERVICE_TIMEOUT_S)

    if plat == "win32":
        run_fn(["schtasks.exe", "/Delete", "/TN", "OpenClaw Gateway", "/F"], SERVICE_TIMEOUT_S)
        resolved_appdata = appdata or (resolved_home / "AppData" / "Roaming")
        resolved_startup = startup_dir or (
            resolved_appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        )
        vbs = resolved_startup / "OpenClaw Gateway.vbs"
        vbs.unlink(missing_ok=True)
    else:
        resolved_systemd = systemd_user_dir or (resolved_home / ".config" / "systemd" / "user")
        unit = resolved_systemd / "openclaw-gateway.service"
        if unit.is_file():
            run_fn(["systemctl", "--user", "stop", "openclaw-gateway.service"], SERVICE_TIMEOUT_S)
            run_fn(["systemctl", "--user", "disable", "openclaw-gateway.service"], SERVICE_TIMEOUT_S)
            unit.unlink(missing_ok=True)
            run_fn(["systemctl", "--user", "daemon-reload"], SERVICE_TIMEOUT_S)

    out(t("Gateway OpenClaw locale fermato e disattivato all'avvio."))
    out(t("Cartella di stato (~/.openclaw: sessioni, memoria, configurazione) conservata intatta."))
    return {"ok": True}


def stop_node_service_safe(
    runner: Run | None = None,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Stops and uninstalls node host service using documented OpenClaw commands."""
    run_fn = runner or run_command
    out(t("Disattivazione servizio nodo OpenClaw..."))
    run_fn(["openclaw", "node", "stop"], SERVICE_TIMEOUT_S)
    run_fn(["openclaw", "node", "uninstall"], SERVICE_TIMEOUT_S)
    return {"ok": True}


def start_node_service_safe(
    runner: Run | None = None,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Starts node host service using documented 'openclaw node start'."""
    run_fn = runner or run_command
    out(t("Riavvio servizio nodo OpenClaw con 'openclaw node start'..."))
    run_fn(["openclaw", "node", "start"], SERVICE_TIMEOUT_S)
    return {"ok": True}


def format_final_summary(
    gateway_url: str,
    node_name: str,
    stopped_items: list[str],
    preserved_items: list[str],
) -> str:
    """Produces the final single-screen operational summary."""
    stopped_text = "; ".join(stopped_items) if stopped_items else t("nessun servizio locale precedente")
    preserved_text = "; ".join(preserved_items) if preserved_items else t("tutti i file locali")

    summary_lines = [
        "============================================================",
        t("Riepilogo passaggio a nodo:"),
        t("  - Gateway: {gw}", gw=gateway_url),
        t("  - Nome nodo: {name}", name=node_name),
        t("  - Servizi fermati: {stopped}", stopped=stopped_text),
        t("  - Dati conservati: {preserved}", preserved=preserved_text),
        "",
        t("Per assegnare compiti a questo nodo nella squadra centrale, aggiungi a squad.yaml sul server:"),
        f"  agents:\n    {node_name}:\n      tools:\n        exec:\n          host: node\n          node: {node_name}",
        "============================================================",
        t("Nodo pronto e operativo."),
    ]
    return "\n".join(summary_lines)
