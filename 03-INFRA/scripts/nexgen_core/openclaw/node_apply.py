"""`nexgen openclaw node --apply`: this machine becomes a node, from here.

One job: take the two facts a person already decided (which gateway, which name)
and run the machine-side steps in the only order that works, stopping at the
first one that fails. The gateway's own side, the device and command approvals,
stays on the gateway and is printed rather than done.

The order matters and was learned the hard way on 2026-10-26: a local gateway
left running keeps a channel poller and a token of its own, and a node started
beside it crash-loops on `AUTH_TOKEN_MISMATCH` until both are retired. So the
retirement comes first, and it is the same code `install.sh --role node` uses.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.openclaw.node_attach import (
    DEFAULT_NODE_ATTACH_TIMEOUT_S,
    check_node_connected,
    connect_node_service,
    wait_for_node_connection,
)
from nexgen_core.openclaw.node_cli import SERVICE_TIMEOUT_S, Run, run_command
from nexgen_core.openclaw.node_link import SERVE_PORT, NodeLink, build_node_link, install_argv
from nexgen_core.openclaw.node_retire import (
    RetireResult,
    describe_config_change,
    drop_in_path,
    prepare_config,
    report,
    retire_local_gateway,
)
from nexgen_core.openclaw.node_status import normalize_gateway_origin
from nexgen_core.openclaw.node_transition import ExistingNodeInfo, detect_existing_node
from nexgen_core.openclaw.profile import GATEWAY_PORT
from nexgen_core.paths import openclaw_config, resolve_home


def _target_origin(host: str, join_target: str | None) -> str:
    """The gateway this machine would join, as the node would record it."""
    return normalize_gateway_origin(join_target or f"https://{host}")


def _refuse(host: str, existing: ExistingNodeInfo, join_target: str | None,
            force: bool, out: Callable[[str], None]) -> int | None:
    """One case only: the machine is a node of another gateway and --force was not given."""
    if not existing.is_node or force:
        return None
    wanted = _target_origin(host, join_target)
    if existing.gateway_origin == wanted:
        return None
    out(t("This machine is already a node of {current}.", current=existing.gateway_origin))
    out(t("The gateway asked for is {wanted}.", wanted=wanted))
    out(t("To move it to another gateway, run the same command with --force."))
    return 1


def _already_there(host: str, existing: ExistingNodeInfo, join_target: str | None,
                   runner: Run | None, out: Callable[[str], None]) -> bool:
    """True when the machine is already a node of this gateway: it is started, not re-enrolled."""
    if not existing.is_node:
        return False
    if existing.gateway_origin != _target_origin(host, join_target):
        return False
    out(t("This machine is already a node of {current}.", current=existing.gateway_origin))
    if existing.is_running:
        out(t("The node service is running: nothing to enroll again."))
        return True
    run_fn = runner or run_command
    out(t("The node service is not running: starting it."))
    run_fn(["openclaw", "node", "start"], SERVICE_TIMEOUT_S)
    return True


def plan_lines(link: NodeLink, port: int, name: str | None, config_path: Path,
               join_target: str | None, drop_in_present: bool) -> list[str]:
    """What the apply would do, in the order it would do it: read only."""
    address = f"https://{link.host}" if link.tailnet else f"{link.host}:{port}"
    lines = [
        t("OpenClaw node for the gateway at {address}", address=address),
        t("- local gateway: stopped and removed, the node host replaces it"),
    ]
    if drop_in_present:
        lines.append(t("- drop-in left without its unit: archived, not deleted"))
    lines.extend(describe_config_change(config_path))
    lines.append(t("- node service: installed and started with host {host} port {port}, session hosting on",
                   host=link.host, port=port))
    if join_target:
        lines.append(t("- enrolment: the join link taken from the file, approved by the gateway in the same step"))
    else:
        lines.append(t("- enrolment: the device waits for approval on the gateway, nothing is approved here"))
    if name:
        lines.append(t("- name shown by the gateway: {name}", name=name))
    return lines


def _finish(link: NodeLink, name: str | None, joined: bool, out: Callable[[str], None]) -> None:
    """The last screen: what to look at, and where the one remaining step lives."""
    label = name or "node"
    out("")
    out(t("Check on this machine: openclaw node status"))
    out(t("Check on the gateway: openclaw nodes describe --node {name}", name=label))
    if joined:
        out(t("The join link is spent: delete the file you kept it in."))
    else:
        out(t("The gateway still has to admit this machine: run 'nexgen openclaw pair' there, "
              "approve the device, then approve the node request."))


def _attach(
    link: NodeLink,
    port: int,
    name: str | None,
    join_target: str | None,
    runner: Run | None,
    wait_probe_fn: Callable[[], bool] | None,
    timeout_s: float,
    out: Callable[[str], None],
) -> int:
    """Enrol this machine the way the person chose: one paste, or two approvals."""
    run_fn = runner or run_command
    if join_target:
        if not connect_node_service(join_target, runner=runner, out=out):
            return 1
        probe = wait_probe_fn or (lambda: check_node_connected(runner=runner))
        connected, _elapsed = wait_for_node_connection(
            probe, node_name=name or "node", timeout_s=timeout_s, emit=out,
        )
        if not connected:
            return 1
        _finish(link, name, True, out)
        return 0

    install = run_fn(install_argv(link.host, port, name, tls=link.tailnet), SERVICE_TIMEOUT_S)
    if install.returncode != 0:
        out(t("Node install failed (exit code {code}).", code=install.returncode))
        return 1
    start = run_fn(["openclaw", "node", "start"], SERVICE_TIMEOUT_S)
    if start.returncode != 0:
        out(t("Node service did not start (exit code {code}). Try 'openclaw node status'.", code=start.returncode))
        return 1
    _finish(link, name, False, out)
    return 0


def apply_node(
    host: str,
    *,
    port: int | None = None,
    name: str | None = None,
    join_target: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    home: Path | None = None,
    platform: str | None = None,
    runner: Run | None = None,
    systemd_user_dir: Path | None = None,
    wait_probe_fn: Callable[[], bool] | None = None,
    timeout_s: float = DEFAULT_NODE_ATTACH_TIMEOUT_S,
    out: Callable[[str], None] = print,
) -> int:
    """Make this machine a node of the gateway at `host`, or say what that would do."""
    resolved_home = resolve_home(home)
    number = int(port) if port is not None else GATEWAY_PORT
    link = build_node_link(host, port=number, name=name, platform=platform)
    effective_port = SERVE_PORT if link.tailnet else link.port
    config_path = openclaw_config(resolved_home)
    existing = detect_existing_node(runner=runner, home=resolved_home)

    if (refusal := _refuse(host, existing, join_target, force, out)) is not None:
        return refusal
    if dry_run:
        for line in plan_lines(link, effective_port, name, config_path, join_target,
                               drop_in_path(resolved_home, systemd_user_dir).is_dir()):
            out(line)
        return 0
    if _already_there(host, existing, join_target, runner, out):
        out(report(RetireResult(None, prepare_config(config_path, out))))
        _finish(link, name, True, out)
        return 0

    result = retire_local_gateway(
        home=resolved_home, platform=platform, runner=runner,
        systemd_user_dir=systemd_user_dir, config_path=config_path, out=out,
    )
    out(report(result))
    return _attach(link, effective_port, name, join_target, runner, wait_probe_fn, timeout_s, out)

