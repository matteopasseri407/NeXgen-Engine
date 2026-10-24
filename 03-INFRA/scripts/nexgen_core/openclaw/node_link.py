"""`nexgen openclaw node`: this machine as a node of a gateway on another PC.

One job: turn "connect this machine to the gateway that lives on the other PC"
into OpenClaw's own commands, in the order a person runs them, with the check
after each one. It proposes and never runs. `openclaw node install` already
writes the systemd user service on Linux, the LaunchAgent on macOS and the
scheduled task on Windows, and a join code is the gateway's to mint, so the
engine prints what exists instead of wrapping it in a second implementation.

Nothing here touches a local gateway. A node host is a process of its own with a
state record of its own, so a laptop that already serves a gateway in loopback
keeps it exactly as it is; what it gains is a second connection outward.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.openclaw import doctor_runtime
from nexgen_core.openclaw.profile import GATEWAY_PORT
from nexgen_core.report import Severity


@dataclass(frozen=True)
class Step:
    """One command, the machine it runs on, and how a person sees that it worked."""

    command: str
    on_gateway: bool = False
    check: str | None = None
    caution: str | None = None


@dataclass(frozen=True)
class Route:
    """One way to enrol the node: its title, why it exists, and every step in order."""

    title: str
    why: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class NodeLink:
    """What it takes to make this machine a node of a gateway on another PC."""

    host: str
    port: int
    routes: tuple[Route, ...]
    notes: tuple[str, ...]


def _linger_step(platform: str, user: str | None, directory: Path | None) -> Step | None:
    """On Linux only, and only while lingering is off: without it the node dies at logout.

    The judgement is the doctor's own check, not a second reading of the same fact,
    so the advice here cannot drift from the line `nexgen doctor` prints.
    """
    outcome = doctor_runtime.check_linger(platform, user, directory)
    if outcome is None or outcome.severity == Severity.OK:
        return None
    return Step(
        command=f"loginctl enable-linger {user}",
        check=f"loginctl show-user {user} --property=Linger",
        caution=t("Without lingering, the systemd user service stops when your last session closes."),
    )


def _install_command(host: str, port: int, name: str | None) -> str:
    """The documented install line, with the display name only when one was given."""
    named = f' --display-name "{name}"' if name else ""
    return f"openclaw node install --host {host} --port {port}{named}"


def _explicit_route(host: str, port: int, name: str | None, linger: Step | None) -> Route:
    """Two approvals, one at a time: the device first, then the commands it may run."""
    steps = [
        Step(
            command=_install_command(host, port, name),
            check="openclaw node status --json",
            caution=t("This writes a user service on this machine; nothing else on the machine changes."),
        ),
        Step(command="openclaw node start", check="openclaw node status"),
    ]
    if linger is not None:
        steps.append(linger)
    steps.extend([
        Step(
            command="nexgen openclaw pair",
            on_gateway=True,
            check=t("the request created by this machine, with the id the next step needs"),
        ),
        Step(
            command="nexgen openclaw pair --approve <device id>",
            on_gateway=True,
            caution=t("Read the request before approving it: the id is this machine's, not another device's."),
            check="nexgen openclaw pair",
        ),
        Step(
            command="openclaw node restart",
            caution=t("Only when the node's log says it paused waiting for pairing: otherwise it reconnects on its own."),
            check="nexgen openclaw pair",
        ),
        Step(
            command="nexgen openclaw pair --approve-node <node request id>",
            on_gateway=True,
            caution=t("The approval lists the commands this node would expose, and those are what you are allowing."),
            check="nexgen openclaw doctor",
        ),
    ])
    return Route(
        title=t("One approval at a time, without a join code"),
        why=t("Two requests with different ids: the device admits the connection, the node request allows commands."),
        steps=tuple(steps),
    )


def _join_code_route(linger: Step | None) -> Route:
    """OpenClaw's one-paste route: the gateway mints a link, the node redeems it as a service."""
    steps = [
        Step(
            command="openclaw devices join-code",
            on_gateway=True,
            caution=t("Single use, ten minutes, administrator access: treat the printed URL like a password."),
            check=t("the command it prints, to be pasted as it is on this machine"),
        ),
        Step(
            command="openclaw connect <url> --service",
            check="openclaw node status --json",
            caution=t("This is the one paste that enrols the device and its first commands together."),
        ),
    ]
    if linger is not None:
        steps.append(linger)
    steps.append(Step(command="nexgen openclaw doctor", on_gateway=True, check="openclaw nodes status"))
    return Route(
        title=t("One paste, with a join code the gateway mints"),
        why=t("OpenClaw approves the device and its first command surface in the same step."),
        steps=tuple(steps),
    )


def _notes(host: str, port: int) -> tuple[str, ...]:
    """What no command can decide for the person, said once and plainly."""
    return (
        t("The gateway on the other PC has to answer on the home network: 'nexgen openclaw setup --lan' writes "
          "gateway.bind 'lan', and 'nexgen openclaw doctor' there says whether it is reachable."),
        t("The address {host} is yours to supply: the engine does not guess another machine's address.", host=host),
        t("Your own gateway is left as it is: the node host is a separate process, so a machine that already "
          "serves OpenClaw in loopback keeps serving it."),
        t("Away from home only a local gateway in loopback is reachable, and a channel such as Telegram lives on "
          "one gateway at a time: it belongs to the machine that stays on."),
        t("Every approval is yours: nothing is approved automatically, and each request names the machine and the "
          "commands it would run."),
        t("Port {port} is the one the gateway listens on; a different one there means passing it here too.", port=port),
    )


def build_node_link(
    host: str,
    *,
    port: int = GATEWAY_PORT,
    name: str | None = None,
    platform: str | None = None,
    user: str | None = None,
    linger_directory: Path | None = None,
) -> NodeLink:
    """Both documented routes to a node, in the order a person runs them.

    `host` is the gateway machine's address, which only the person knows; `name` is
    the label the gateway will show for this machine, and it stays out of the
    install line when it was not given, because OpenClaw has a default of its own.
    """
    address = str(host).strip()
    if not address:
        raise ValueError("the gateway machine's address cannot be empty")
    number = int(port)
    label = str(name).strip() if name else None
    system = platform or sys.platform
    account = user or os.environ.get("USER") or os.environ.get("LOGNAME")
    linger = _linger_step(system, account, linger_directory)
    return NodeLink(
        host=address,
        port=number,
        routes=(
            _join_code_route(linger),
            _explicit_route(address, number, label, linger),
        ),
        notes=_notes(address, number),
    )


def render_node_link(link: NodeLink) -> str:
    """The proposal as a person reads it: every route numbered, each step with its check."""
    lines = [
        t("OpenClaw node for the gateway at {host}:{port}", host=link.host, port=link.port),
        t("Nothing has been changed: these are OpenClaw's own commands, in the order they work."),
        "",
    ]
    for route in link.routes:
        lines.append(route.title)
        lines.append(f"  {route.why}")
        for number, step in enumerate(route.steps, start=1):
            where = t("on the gateway machine") if step.on_gateway else t("on this machine")
            lines.append(f"  {number}. {step.command}  ({where})")
            if step.caution:
                lines.append(f"     ! {step.caution}")
            if step.check:
                lines.append("     " + t("check: {command}", command=step.check))
        lines.append("")
    lines.append(t("What stays yours:"))
    lines.extend(f"  - {note}" for note in link.notes)
    return "\n".join(lines)
