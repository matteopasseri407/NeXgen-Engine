"""The Windows app, in the setup: where its gateway is, and how it joins this one.

One job: the two steps and the one line that concern OpenClaw Companion. Before
anything is installed, a machine where only the app runs the gateway stops the run
instead of installing a second one beside it; at the end, the app is told where
this gateway answers and the request it leaves is approved only with an explicit
yes. Nothing here starts, stops or writes the app's own distribution.

These were the last part of `setup_steps.py` until that file filled up. Every
program still arrives through `Setup.run` and every service action through
`Setup.service`, exactly as there.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw import setup_commands
from nexgen_core.openclaw.companion import APP
from nexgen_core.openclaw.detect import Detection
from nexgen_core.openclaw.setup_steps import Setup
from nexgen_core.openclaw_pairing import approve as pairing_approve
from nexgen_core.openclaw_pairing import listed as pairing_listed
from nexgen_core.processes import command_line_text


def app_gateway_stop(setup: Setup, snapshot: Detection, *, node_gateway: bool = False) -> bool:
    """Case (b): the app runs the only gateway here, and this engine cannot write it.

    Without `node_gateway` nothing has been installed and nothing has been
    written yet: a native gateway beside the app's one would fight over the
    same port and the same state, so the run stops and names the road this
    engine supports and the two ways forward. With `node_gateway` the run goes
    on only if the port is free, because freeing it is the person's job in the
    app: the app's distribution is never started, stopped or written either way.
    """
    gateway = snapshot.companion
    if gateway.situation(snapshot.openclaw.installed) != APP:
        return False
    if node_gateway:
        return _app_gateway_takes_the_port(setup)
    setup.out(t("The OpenClaw Companion app runs the gateway on this PC ({signals}) and there is no native one.",
                signals=", ".join(gateway.signals)))
    setup.out(t("This version of the engine configures the native Node gateway, which is the road it supports, not "
                "the app's one: nothing was installed and nothing was written, because a native gateway beside it "
                "would fight over the same port."))
    setup.out(t("To use the app with the engine, open Connections in OpenClaw Companion and choose 'a local Gateway "
                "on this PC'."))
    setup.out(t("Two ways forward: use the app as it is, or install the native gateway with {command} and run "
                "this setup again.",
                command=command_line_text(setup_commands.install_argv(
                    setup.which("npm") or "npm", setup.pinned or "<pin>",
                ))))
    setup.out(t("To take the second road while the app's gateway is still here, stop it in OpenClaw Companion and "
                "run this setup again with --node-gateway."))
    return True


def _app_gateway_takes_the_port(setup: Setup) -> bool:
    """`--node-gateway`: go on only when the app's gateway has let the port go."""
    port = setup.service.port
    if setup.service.port_probe(port, "127.0.0.1"):
        setup.out(t("Something answers on 127.0.0.1:{port} already: stop the app's gateway in OpenClaw Companion "
                    "and run this setup again.", port=port))
        setup.out(t("Two gateways cannot share the port, and this setup never starts, stops or writes the app's "
                    "distribution."))
        return True
    setup.out(t("Going ahead with the native gateway: 127.0.0.1:{port} is free.", port=port))
    setup.out(t("The app can be pointed at it: in OpenClaw Companion open Connections and choose 'a local Gateway "
                "on this PC'."))
    return False


def device_line(device: Mapping[str, Any]) -> str:
    """One waiting device as the person reads it: its id, then what it says it is."""
    label = device.get("summary") or device.get("status") or ""
    return f"  {device.get('id')}  {label}".rstrip()


def app_step(setup: Setup, snapshot: Detection, ask: Callable[[str], bool]) -> None:
    """The Windows app: how to point it here, and the request it leaves waiting.

    Only when the app is installed and owns no gateway of its own: the setup has
    just configured the native one, so the app has exactly one place to connect
    to. A request already waiting is shown with what would be approved and
    approved only after the person says yes; nothing is approved because the run
    was started with `--yes`, and nothing is approved without a terminal.
    """
    gateway = snapshot.companion
    if not gateway.present or gateway.owns_local_gateway or gateway.points_remote:
        return
    if gateway.names_no_gateway():
        setup.out(t("In OpenClaw Companion open Connections and choose 'a local Gateway on this PC': the gateway "
                    "this setup configured answers on 127.0.0.1:{port}.", port=setup.service.port))
    approve_app_request(setup, ask)


def approve_app_request(setup: Setup, ask: Callable[[str], bool]) -> None:
    """The one request the app left, approved only with an explicit yes."""
    devices = pairing_listed(setup.run)
    if devices is None:
        setup.out(t("The gateway cannot list its devices now: 'nexgen openclaw pair' shows the request and "
                    "approves it when the gateway answers."))
        return
    if not devices:
        setup.out(t("No request from the app is waiting yet: when the app asks, 'nexgen openclaw pair' lists the "
                    "request and 'nexgen openclaw pair --approve <id>' approves it."))
        return
    setup.out(t("Requests waiting for approval:"))
    for device in devices:
        setup.out(device_line(device))
    if len(devices) > 1:
        setup.out(t("More than one request is waiting: 'nexgen openclaw pair' lists them, and "
                    "'nexgen openclaw pair --approve <id>' approves the one you choose."))
        return
    device_id = str(devices[0].get("id") or "").strip()
    if not device_id:
        setup.out(t("A request is waiting but its id cannot be read: 'nexgen openclaw pair' shows it."))
        return
    if not ask(t("Approve this device?")):
        setup.out(t("Nothing approved: 'nexgen openclaw pair --approve {id}' approves it later.", id=device_id))
        return
    outcome = pairing_approve(device_id, runner=setup.run)
    if outcome.get("ok"):
        setup.out(t("Device {id} approved.", id=device_id))
        return
    setup.out(t("The approval failed ({reason}): 'nexgen openclaw pair --approve {id}' retries it.",
                reason=outcome.get("error") or t("no reason given"), id=device_id))
