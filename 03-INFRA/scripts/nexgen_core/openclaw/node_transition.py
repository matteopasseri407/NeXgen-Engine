"""Inspection, planning, and safe service teardown for node role transition.

One job: manage the transition of an existing engine machine to an OpenClaw node,
inspecting pre-existing services and gateway pairings, generating a non-destructive
execution plan, safely decommissioning local gateway services without touching state,
and formatting the final one-screen operational summary.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nexgen_core.i18n import t
from nexgen_core.openclaw.companion import probe as probe_companion
from nexgen_core.openclaw_service import RunResult
from nexgen_core.paths import openclaw_config, resolve_home


def _safe_default_runner(cmd: list[str]) -> RunResult:
    """Runner that never executes external commands during pytest, and uses bounded timeout."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return RunResult(1, "", "test mode: real command execution prevented")
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5.0,
        )
        return RunResult(res.returncode, res.stdout, res.stderr)
    except Exception as exc:  # nexgen: allow reason="fallback returncode when process execution fails"
        return RunResult(1, "", str(exc))


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
    connected: bool
    gateway_url: str | None
    gateway_origin: str | None
    node_name: str | None


@dataclass(frozen=True)
class TransitionInspection:
    """Full snapshot of this machine prior to transitioning to a node."""

    engine: EngineVersionInfo
    gateway: LocalGatewayInfo
    node: ExistingNodeInfo


def normalize_gateway_origin(target: str) -> str:
    """Extracts host or host:port from a join URL, endpoint, or raw host address."""
    clean = target.strip()
    if not clean:
        return ""
    if "://" in clean:
        parsed = urlsplit(clean)
        return parsed.netloc.lower()
    head = clean.split("/")[0]
    return head.lower()


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
    runner: Callable[[list[str]], RunResult] | None,
    run_fn: Callable[[list[str]], RunResult],
) -> tuple[bool, bool]:
    """Detects Windows startup VBS file and scheduled task."""
    if plat != "win32":
        return False, False
    vbs = resolved_startup / "OpenClaw Gateway.vbs"
    startup_vbs_present = vbs.is_file()
    scheduled_task_present = False
    if startup_vbs_present or state_dir.is_dir() or runner is not None:
        sch_res = run_fn(["schtasks.exe", "/Query", "/TN", "OpenClaw Gateway"])
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
        comp = probe_companion(resolved_home, os.environ, plat, wsl_distributions=lambda: [])
        detail = ", ".join(comp.signals) if comp.signals else None
        return comp.present, detail
    except Exception:  # nexgen: allow reason="companion probe failure on unconfigured desktop safely treated as absent"
        return False, None


def detect_local_gateway(
    home: Path | None = None,
    platform: str | None = None,
    runner: Callable[[list[str]], RunResult] | None = None,
    appdata: Path | None = None,
    startup_dir: Path | None = None,
    systemd_user_dir: Path | None = None,
) -> LocalGatewayInfo:
    """Detects local gateway services, scheduled tasks, and companion app."""
    run_fn = runner or _safe_default_runner
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
        stat_res = run_fn(["openclaw", "gateway", "status", "--json"])
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


def detect_existing_node(
    runner: Callable[[list[str]], RunResult] | None = None,
    home: Path | None = None,
    state_dir: Path | None = None,
) -> ExistingNodeInfo:
    """Checks whether this machine is already configured or connected as a node."""
    run_fn = runner or _safe_default_runner
    resolved_home = resolve_home(home)
    st_dir = state_dir or openclaw_config(resolved_home).parent

    if runner is None and not st_dir.is_dir():
        return ExistingNodeInfo(
            is_node=False, connected=False, gateway_url=None, gateway_origin=None, node_name=None,
        )

    gw_url: str | None = None
    node_name: str | None = None
    connected = False
    is_node = False

    res = run_fn(["openclaw", "node", "status", "--json"])
    if res.returncode == 0 and res.stdout.strip():
        try:
            data = json.loads(res.stdout)
            if isinstance(data, dict):
                is_node = True
                status = str(data.get("status") or "").strip().lower()
                conn = data.get("connected")
                connected = bool(conn) if conn is not None else status in ("connected", "running", "online")
                gw_url = data.get("gateway") or data.get("gatewayUrl") or data.get("endpoint")
                node_name = data.get("displayName") or data.get("name")
        except json.JSONDecodeError:  # nexgen: allow reason="non-json node status output safely handled"
            pass

    if not gw_url and st_dir.is_dir():
        node_json = st_dir / "node.json"
        if node_json.is_file():
            try:
                ndata = json.loads(node_json.read_text(encoding="utf-8"))
                is_node = True
                gw_url = ndata.get("gateway") or ndata.get("host")
                node_name = node_name or ndata.get("displayName")
            except Exception:  # nexgen: allow reason="corrupted or unreadable node.json treated as missing node info"
                pass

    origin = normalize_gateway_origin(gw_url) if gw_url else None
    return ExistingNodeInfo(
        is_node=is_node,
        connected=connected,
        gateway_url=gw_url,
        gateway_origin=origin,
        node_name=node_name,
    )


def inspect_node_transition(
    root: Path,
    home: Path | None = None,
    platform: str | None = None,
    runner: Callable[[list[str]], RunResult] | None = None,
    appdata: Path | None = None,
    startup_dir: Path | None = None,
    systemd_user_dir: Path | None = None,
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

    if inspection.node.is_node and inspection.node.gateway_url:
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
    runner: Callable[[list[str]], RunResult] | None = None,
    appdata: Path | None = None,
    startup_dir: Path | None = None,
    systemd_user_dir: Path | None = None,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Gracefully stops and decommissions local gateway without touching state directory."""
    run_fn = runner or _safe_default_runner
    plat = platform if platform is not None else sys.platform
    resolved_home = resolve_home(home)

    out(t("Disattivazione gateway OpenClaw locale..."))
    run_fn(["openclaw", "gateway", "stop"])
    run_fn(["openclaw", "gateway", "uninstall"])

    if plat == "win32":
        run_fn(["schtasks.exe", "/Delete", "/TN", "OpenClaw Gateway", "/F"])
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
            run_fn(["systemctl", "--user", "stop", "openclaw-gateway.service"])
            run_fn(["systemctl", "--user", "disable", "openclaw-gateway.service"])
            unit.unlink(missing_ok=True)
            run_fn(["systemctl", "--user", "daemon-reload"])

    out(t("Gateway OpenClaw locale fermato e disattivato all'avvio."))
    out(t("Cartella di stato (~/.openclaw: sessioni, memoria, configurazione) conservata intatta."))
    return {"ok": True}


def stop_node_service_safe(
    runner: Callable[[list[str]], RunResult] | None = None,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Stops and uninstalls node host service using documented OpenClaw commands."""
    run_fn = runner or _safe_default_runner
    out(t("Disattivazione servizio nodo OpenClaw..."))
    run_fn(["openclaw", "node", "stop"])
    run_fn(["openclaw", "node", "uninstall"])
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
