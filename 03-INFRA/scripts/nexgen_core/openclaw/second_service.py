"""The gateway registration that is not the service this machine runs.

One job: say, in the two voices the engine has, that something else on this
machine can start a gateway too -- as one doctor line, and as the step the setup
prints when it finds a service already installed. The engine never removes it. It
did not create it (a registration from an older release, a second profile, or a
node host whose launcher only looks like a gateway), so the only honest act is to
name it, hand back the command OpenClaw's own inventory implies, and let the
person decide.

The facts come from `openclaw gateway status --deep --json`, because the names
alone cannot tell the two apart: on Windows the service OpenClaw manages and the
one an older engine drove are both called "OpenClaw Gateway". That command costs
a full service inventory (about 20 seconds cold on Windows), so the doctor asks
for it only where a person ran `nexgen openclaw doctor`, and the setup asks for
it only on a machine that already had a service before the run.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw_service import OpenClawService, removal_commands
from nexgen_core.report import CheckOutcome, Severity

CHECK_ID = "openclaw.second_service"


def _line(registration: Mapping[str, Any]) -> str:
    """One registration as a person reads it: the name, where it lives, what it runs."""
    return t("{label} ({scope}): {detail}",
             label=registration.get("label") or t("(unnamed)"),
             scope=registration.get("scope") or t("user"),
             detail=registration.get("detail") or t("(not inspected)"))


def _commands(registrations: list[Mapping[str, Any]]) -> list[str]:
    """Every removal step, in the order the registrations were reported."""
    return [command for registration in registrations for command in removal_commands(registration)]


def check(registrations: list[Mapping[str, Any]] | None) -> CheckOutcome | None:
    """The doctor line for a second registration, or nothing when the machine has one service.

    A warning, not a failure: the managed service works, and the second
    registration may be one its owner meant to keep. The action is the command
    that removes it, printed rather than run.
    """
    if not registrations:
        return None
    commands = _commands(registrations)
    if commands:
        action = t("Keep it if it is a second profile you meant. To remove it, run: {commands}",
                   commands=" && ".join(commands))
    else:
        action = t("Keep it if it is a second profile you meant; otherwise inspect it with 'openclaw doctor', "
                   "which offers to remove a registration from an older release.")
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.WARN,
        message=t("Another registration can start a gateway on this machine ({registrations}): "
                  "one gateway per machine is what keeps two of them off one port.",
                  registrations="; ".join(_line(registration) for registration in registrations)),
        action=action,
    )


def outcome(reader: Callable[[], list[dict[str, Any]]] | None) -> list[CheckOutcome]:
    """The line for a caller that can look for a second registration, or nothing when nobody asked.

    Looking costs OpenClaw's whole deep service inventory, so an absent reader is
    "nobody asked", and silence is the honest answer.
    """
    if reader is None:
        return []
    found = check(reader())
    return [found] if found is not None else []


def setup_step(service: OpenClawService, out: Callable[[str], None]) -> None:
    """The setup's own words for the same fact: what is there, and the step that removes it.

    Called after the managed service answers, on a machine that already had one
    before this run: a first install has no older release to have left anything.
    """
    registrations = service.extra_services()
    if not registrations:
        return
    out(t("Another registration for the gateway is still on this machine: the managed service is not the only one."))
    for registration in registrations:
        out("  " + _line(registration))
        for command in removal_commands(registration):
            out("  $ " + command)
    out(t("The engine leaves it alone: run the lines above if you do not need it."))
