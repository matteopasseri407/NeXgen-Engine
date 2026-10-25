"""Three roles for NeXgen Engine machine initialization.

One job: configure a machine into one of three distinct roles:
- server: always-on OpenClaw gateway on headless Linux (loopback + Tailscale Serve).
- node: PC connecting to an existing gateway, registering its agent via RPC.
- standalone: local PC running its own gateway.

Idempotent by contract: re-running any role on an already configured machine
causes no damage and maintains configuration alignment.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexgen_core.first_run import (
    align_now as _default_align,
    commit_setup,
    remaining_problems as _default_remaining_problems,
    seed_skill_manifest,
    write_remotes,
    write_user_profile,
)
from nexgen_core.i18n import t
from nexgen_core.openclaw.node_attach import (
    DEFAULT_NODE_ATTACH_TIMEOUT_S,
    FULL_EXEC_POLICY,
    apply_exec_policy,
)
from nexgen_core.openclaw.node_cli import Run
from nexgen_core.openclaw.node_transition import (
    detect_existing_node,
    format_final_summary,
    inspect_node_transition,
    normalize_gateway_origin,
    plan_node_transition,
    start_node_service_safe,
    stop_local_gateway_safe,
    stop_node_service_safe,
)
from nexgen_core.paths import resolve_home


def _install_launchers_step(root: Path, out: Callable[[str], None]) -> None:
    scripts_dir = root / "03-INFRA" / "scripts"
    if (scripts_dir / "nexgen_core").is_dir():
        try:
            from nexgen_core.shims import install_shims

            installed = install_shims(scripts_dir=scripts_dir, home=resolve_home())
            out(t("{count} commands installed in ~/.local/bin", count=len(installed)))
        except Exception as exc:  # nexgen: allow reason="shim install failure handled gracefully during role bootstrap"
            out(t("commands not installed ({error})", error=exc))


def _default_openclaw_setup(**kwargs: Any) -> int:
    from nexgen_core.openclaw.setup import run_setup

    return run_setup(**kwargs)


def _default_update(root: Path, out: Callable[[str], None]) -> int:
    try:
        from nexgen_core import updater

        out(t("Updating NeXgen engine to channel latest..."))
        return updater.main(["--yes"])
    except Exception as exc:  # nexgen: allow reason="update errors reported without failing subsequent role config"
        out(t("Engine update skipped ({error}).", error=exc))
        return 0


def _sync_node_role(
    root: Path,
    out: Callable[[str], None],
    align_fn: Callable[[], tuple[bool, str]] | None = None,
) -> int:
    profile = "MINIMAL"
    write_user_profile(
        root,
        profile=profile,
        clis=["claude", "codex", "opencode", "antigravity"],
        machines="secondary (node)",
    )
    write_remotes(root, profile=profile)
    seed_skill_manifest(root)
    _install_launchers_step(root, out)
    aligner = align_fn or _default_align
    aligned, message = aligner()
    out(t("Synchronized CLI, skills and MCP from engine: {msg}", msg=message))
    return 0 if aligned else 1


def run_standalone_role(
    root: Path,
    home: Path | None = None,
    setup_fn: Callable[..., int] | None = None,
    align_fn: Callable[[], tuple[bool, str]] | None = None,
    problems_fn: Callable[[], tuple[int, list[str]]] | None = None,
    runner: Run | None = None,
    platform: str | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Standalone role: local PC with its own gateway."""
    out(t("NeXgen Engine · standalone role (local PC with own gateway)"))
    home_dir = resolve_home(home)
    profile = "MINIMAL"

    node_info = detect_existing_node(runner=runner, home=home_dir)
    if node_info.is_node or runner is not None:
        stop_node_service_safe(runner=runner, out=out)

    write_user_profile(
        root,
        profile=profile,
        clis=["claude", "codex", "opencode", "antigravity", "openclaw"],
        machines="primary (this one)",
    )
    write_remotes(root, profile=profile)
    seed_skill_manifest(root)
    _install_launchers_step(root, out)

    commit_setup(root, [
        root / "99-INDEX" / "USER-PROFILE.md",
        root / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml",
        root / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml",
    ])

    run_setup = setup_fn or _default_openclaw_setup
    out(t("Configuring OpenClaw standalone gateway..."))
    setup_code = run_setup(yes=True, server=False, home=home_dir)
    if setup_code != 0:
        out(t("OpenClaw setup returned non-zero code {code}", code=setup_code))

    aligner = align_fn or _default_align
    aligned, message = aligner()
    out(t("Alignment: {msg}", msg=message))

    checker = problems_fn or _default_remaining_problems
    count, _ = checker()
    if count > 0:
        out(t("{count} problem(s) remain: run 'nexgen doctor'", count=count))
        return 1
    out(t("Standalone machine ready."))
    return 0


def run_server_role(
    root: Path,
    home: Path | None = None,
    yes_i_know: bool = False,
    setup_fn: Callable[..., int] | None = None,
    align_fn: Callable[[], tuple[bool, str]] | None = None,
    problems_fn: Callable[[], tuple[int, list[str]]] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Server role: always-on OpenClaw gateway on headless Linux."""
    out(t("NeXgen Engine · server role (always-on headless Linux gateway)"))
    home_dir = resolve_home(home)
    profile = "MULTI"

    write_user_profile(root, profile=profile, clis=["openclaw"], machines="primary (server)")
    write_remotes(root, profile=profile)
    seed_skill_manifest(root)
    _install_launchers_step(root, out)

    commit_setup(root, [
        root / "99-INDEX" / "USER-PROFILE.md",
        root / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml",
        root / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml",
    ])

    run_setup = setup_fn or _default_openclaw_setup
    out(t("Configuring OpenClaw server gateway (loopback + Tailscale Serve)..."))
    setup_code = run_setup(yes=True, server=True, yes_i_know=yes_i_know, home=home_dir)
    if setup_code != 0:
        out(t("OpenClaw server setup failed (exit code {code})", code=setup_code))
        return setup_code

    aligner = align_fn or _default_align
    aligned, message = aligner()
    out(t("Alignment: {msg}", msg=message))

    checker = problems_fn or _default_remaining_problems
    count, _ = checker()
    if count > 0:
        out(t("{count} problem(s) remain: run 'nexgen doctor'", count=count))
        return 1
    out(t("Server gateway ready."))
    return 0


def _handle_existing_node(
    inspection: Any,
    join_target: str | None,
    force: bool,
    root: Path,
    out: Callable[[str], None],
    align_fn: Callable[[], tuple[bool, str]] | None,
    runner: Run | None = None,
) -> tuple[bool, int]:
    """Handles existing node checks. Returns (handled, exit_code)."""
    if not (inspection.node.is_node and inspection.node.gateway_origin):
        return False, 0
    cur_origin = inspection.node.gateway_origin
    if join_target:
        new_origin = normalize_gateway_origin(join_target)
        if new_origin == cur_origin:
            out(t("Questa macchina è già nodo del gateway {gw}.", gw=cur_origin))
            if not inspection.node.is_running:
                start_node_service_safe(runner=runner, out=out)
            return True, _sync_node_role(root, out, align_fn)
        if not force:
            out(t("Questa macchina è già nodo del gateway {gw}.", gw=cur_origin))
            out(t("Il link specificato punta a un gateway diverso: {target}.", target=new_origin))
            out(t("Per confermare il cambio di gateway, rilancia il comando con il flag --force."))
            return True, 1
    else:
        out(t("Questa macchina è già nodo del gateway {gw}.", gw=cur_origin))
        if not inspection.node.is_running:
            start_node_service_safe(runner=runner, out=out)
        return True, _sync_node_role(root, out, align_fn)
    return False, 0


def _show_no_join_instructions(name: str, out: Callable[[str], None]) -> int:
    """Emits guided recovery instructions when no join URL was provided."""
    out(t("Nessun link d'ingresso specificato. Crea un codice monouso sul server gateway:"))
    out(t("  openclaw devices join-code"))
    out(t("Poi esegui su questo PC:"))
    out(t("  install.sh --role node --join <join-url>"))
    out(t("oppure (Windows PowerShell):"))
    out(t("  .\\install.ps1 -Role node -Join <join-url>"))
    out(t("oppure direttamente:"))
    out(t("  openclaw connect <join-url> --service --session-host"))
    out(t("Per assegnare compiti a questo nodo nella squadra centrale, aggiungi a squad.yaml sul server:"))
    out(f"  agents:\n    {name}:\n      tools:\n        exec:\n          host: node\n          node: {name}")
    return 0


def _stop_local_gateway_step(
    inspection: Any,
    home: Path | None,
    platform: str | None,
    runner: Run | None,
    appdata: Path | None,
    startup_dir: Path | None,
    systemd_user_dir: Path | None,
    out: Callable[[str], None],
) -> list[str]:
    """Gracefully shuts down local gateway if one is present."""
    if not inspection.gateway.present:
        return []
    stop_local_gateway_safe(
        home=home,
        platform=platform,
        runner=runner,
        appdata=appdata,
        startup_dir=startup_dir,
        systemd_user_dir=systemd_user_dir,
        out=out,
    )
    return [t("gateway OpenClaw locale disattivato")]


def _attach_and_wait(
    join_target: str,
    home: Path | None,
    runner: Run | None,
    wait_probe_fn: Callable[[], bool] | None,
    name: str,
    timeout_s: float,
    out: Callable[[str], None],
) -> int:
    """Connects node as an OS service and waits for connection verification."""
    from nexgen_core.openclaw.node_attach import (
        check_node_connected,
        connect_node_service,
        wait_for_node_connection,
    )

    ok = connect_node_service(join_target, home=home, runner=runner, out=out)
    if not ok:
        return 1
    probe = wait_probe_fn or (lambda: check_node_connected(runner=runner))
    connected, _ = wait_for_node_connection(probe, node_name=name, timeout_s=timeout_s, emit=out)
    return 0 if connected else 1


def _show_final_summary(
    join_target: str,
    name: str,
    stopped_items: list[str],
    out: Callable[[str], None],
) -> None:
    """Formats and prints the single-screen final summary."""
    gw_display = normalize_gateway_origin(join_target) or join_target
    summary = format_final_summary(
        gateway_url=gw_display,
        node_name=name,
        stopped_items=stopped_items,
        preserved_items=[
            t("cartella ~/.openclaw con configurazione, sessioni e memoria"),
            t("agenti, skill e MCP locali"),
        ],
    )
    out(summary)


def _refuse_unknown_exec_policy(policy: str | None, out: Callable[[str], None]) -> int | None:
    """Exit code 2 for a policy this role does not apply, None when it may go ahead.

    Separated from the role so the role itself stays under its own size ceiling,
    and so the refusal happens before anything on the machine is touched.
    """
    if policy and policy != FULL_EXEC_POLICY:
        out(t("Unknown exec policy '{policy}': this role applies only '{only}'.",
              policy=policy, only=FULL_EXEC_POLICY))
        return 2
    return None


def _execute_node_transition(
    root: Path,
    home: Path | None,
    inspection: Any,
    join_target: str,
    name: str,
    platform: str | None,
    runner: Run | None,
    appdata: Path | None,
    startup_dir: Path | None,
    systemd_user_dir: Path | None,
    update_fn: Any,
    align_fn: Any,
    wait_probe_fn: Any,
    timeout_s: float,
    out: Callable[[str], None],
    exec_policy: str | None = None,
    exec_runner: Callable[[list[str], str], Any] | None = None,
) -> int:
    """Executes update, teardown, sync, and attachment steps of node transition."""
    out(t("NeXgen Engine · passaggio al ruolo nodo (collegamento al gateway centrale)"))
    if (root / "VERSION").is_file():
        (update_fn or _default_update)(root=root, out=out)

    stopped = _stop_local_gateway_step(
        inspection, home, platform, runner, appdata, startup_dir, systemd_user_dir, out,
    )
    sync_code = _sync_node_role(root, out, align_fn)
    if sync_code != 0:
        return sync_code

    code = _attach_and_wait(join_target, home, runner, wait_probe_fn, name, timeout_s, out)
    if code != 0:
        return code
    if exec_policy and not apply_exec_policy(runner=exec_runner, out=out):
        out(t("The node is attached, but its exec approvals still stop on a prompt nobody answers."))
        return 1

    _show_final_summary(join_target, name, stopped, out)
    return 0


def run_node_role(
    root: Path,
    home: Path | None = None,
    target: str | None = None,
    join: str | None = None,
    gateway_url: str | None = None,
    node_name: str | None = None,
    check: bool = False,
    force: bool = False,
    update_fn: Callable[..., int] | None = None,
    align_fn: Callable[[], tuple[bool, str]] | None = None,
    runner: Run | None = None,
    wait_probe_fn: Callable[[], bool] | None = None,
    timeout_s: float = DEFAULT_NODE_ATTACH_TIMEOUT_S,
    platform: str | None = None,
    appdata: Path | None = None,
    startup_dir: Path | None = None,
    systemd_user_dir: Path | None = None,
    exec_policy: str | None = None,
    exec_runner: Callable[[list[str], str], Any] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Node role: connect to an existing gateway as a headless node host.

    `exec_policy` is the explicit "and this PC never stops on an approval" answer:
    without it the node's own exec approvals are not touched, because they are the
    person's. With it, the documented never-prompt body is written locally once the
    node is attached, and the summary says so.
    """
    if (refused := _refuse_unknown_exec_policy(exec_policy, out)) is not None:
        return refused
    inspection = inspect_node_transition(
        root=root, home=home, platform=platform, runner=runner,
        appdata=appdata, startup_dir=startup_dir, systemd_user_dir=systemd_user_dir,
        out=out,
    )
    if check:
        for line in plan_node_transition(inspection):
            out(line)
        return 0 if inspection.node.readable else 1

    if not inspection.node.readable:
        out(t("Passaggio a nodo interrotto: stato del nodo non leggibile ({reason}).",
              reason=inspection.node.unreadable_reason))
        return 1

    join_target = join or target or gateway_url or os.environ.get("NEXGEN_JOIN_URL")
    name = node_name or "node"

    handled, code = _handle_existing_node(inspection, join_target, force, root, out, align_fn, runner=runner)
    if handled:
        return code
    if not join_target:
        return _show_no_join_instructions(name, out)

    return _execute_node_transition(
        root=root, home=home, inspection=inspection, join_target=join_target, name=name,
        platform=platform, runner=runner, appdata=appdata, startup_dir=startup_dir,
        systemd_user_dir=systemd_user_dir, update_fn=update_fn, align_fn=align_fn,
        wait_probe_fn=wait_probe_fn, timeout_s=timeout_s, out=out,
        exec_policy=exec_policy, exec_runner=exec_runner,
    )
