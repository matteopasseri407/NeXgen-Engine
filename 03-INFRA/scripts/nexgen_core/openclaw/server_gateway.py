"""A gateway on a tailnet: whether this host may carry one, and how it is reached.

One job: own the two questions that exist only because the gateway may live on
a machine with no screen. Before a `--server` setup installs anything, this
module asks the host whether it may host a gateway at all; afterwards the
doctor asks the configuration how that gateway is reachable. Both live here so
the preflight and the report cannot disagree about what "public" means.

The facts are asked, never assumed: `tailscale status --json` reports whether
the CLI is logged in (`BackendState`) and `sudo -n true` with `id` report the
privileges of the account that ran the setup. Nothing here installs, starts or
writes anything; it returns facts and, when the machine is not ready, the one
sentence the person can act on.

OpenClaw's own documentation is the source of the recommended pair: a loopback
listener plus `gateway.tailscale.mode: "serve"`, with "the tailscale CLI
installed and logged in" as its prerequisite (`gateway/tailscale.md`,
`gateway/stable-https-url.md`).
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.setup_steps import Run, Setup
from nexgen_core.openclaw_service import RunResult
from nexgen_core.report import CheckOutcome, Severity

__all__ = ["Privileges", "Tailscale", "exposure", "privileges", "server_stop", "tailscale_state"]

#: Where a person installs Tailscale when the server does not have it yet.
TAILSCALE_URL = "https://tailscale.com/download"

#: The binds that never leave this machine. Everything else is reached on a
#: network, and the network decides who may dial it.
LOOPBACK_BINDS = frozenset({"loopback", "127.0.0.1", "::1", "localhost"})

#: The one bind that means "the home network" and nothing wider.
HOME_BIND = "lan"

#: A direct listener on the tailnet address: private, but without HTTPS in front.
TAILNET_BIND = "tailnet"


@dataclass(frozen=True)
class Tailscale:
    """What the CLI reports about itself: present, and logged in or not."""

    installed: bool
    connected: bool = False
    #: `BackendState` (`Running`, `NeedsLogin`, `Stopped`, `NoState`…) or None.
    state: str | None = None


@dataclass(frozen=True)
class Privileges:
    """The powers a gateway with agents must not keep, read from the account."""

    root: bool = False
    passwordless_sudo: bool = False
    docker_group: bool = False

    @property
    def too_strong(self) -> bool:
        """Whether any of the three is held: one is enough to become root."""
        return self.root or self.passwordless_sudo or self.docker_group


def _ask(run: Run, argv: list[str], env: Mapping[str, str] | None) -> RunResult | None:
    """One question to a program, and None when the program cannot be asked."""
    try:
        return run(argv, env)
    except (OSError, ValueError):  # nexgen: allow reason="a program that cannot run is an unknown answer, and the caller turns it into a sentence"
        return None


def _backend_state(text: str | None) -> str | None:
    """`BackendState` from `tailscale status --json`, or None when it is not JSON."""
    try:
        document = json.loads(text or "")
    except (TypeError, ValueError):  # nexgen: allow reason="an older tailscale prints prose; its exit code is then the answer"
        return None
    state = document.get("BackendState") if isinstance(document, dict) else None
    return str(state) if isinstance(state, str) and state else None


def tailscale_state(binary: str, run: Run, env: Mapping[str, str] | None) -> Tailscale:
    """Whether Tailscale is installed and logged in, from its own status command.

    `Binary` is the path the caller resolved; an empty one means the CLI is not
    on PATH and nothing is run. A build too old to answer in JSON is read from
    its exit code, which is the only signal it gives.
    """
    if not binary:
        return Tailscale(installed=False)
    result = _ask(run, [binary, "status", "--json"], env)
    if result is None:
        return Tailscale(installed=False)
    state = _backend_state(result.stdout)
    if state is None:
        return Tailscale(installed=True, connected=result.returncode == 0, state=None)
    return Tailscale(installed=True, connected=state == "Running", state=state)


def privileges(
    which: Callable[[str], str | None],
    run: Run,
    env: Mapping[str, str] | None,
) -> Privileges:
    """Root, passwordless sudo, and the docker group: the three roads to root."""
    identifier = which("id")
    uid = _ask(run, [identifier or "id", "-u"], env) if identifier else None
    groups = _ask(run, [identifier, "-nG"], env) if identifier else None
    sudo = which("sudo")
    sudo_result = _ask(run, [sudo, "-n", "true"], env) if sudo else None
    names = {name for name in ((groups.stdout if groups is not None else "") or "").split()}
    return Privileges(
        root=(uid.stdout or "").strip() == "0" if uid is not None else False,
        passwordless_sudo=sudo_result is not None and sudo_result.returncode == 0,
        docker_group="docker" in names,
    )


def _platform(setup: Setup) -> str:
    """The system this run is deciding for, with the caller's word first."""
    return setup.platform or sys.platform


def server_stop(setup: Setup, *, yes_i_know: bool = False) -> bool:
    """True when the run stops here: this machine cannot host a server gateway yet.

    The order is the order a person can fix things in: the wrong system first,
    because nothing else applies there; then the address, because without
    Tailscale there is no way to reach the gateway at all; then the account,
    because a gateway whose agents can become root can lose the machine.
    """
    setup.out(t("Server mode: the gateway stays on loopback and Tailscale Serve publishes it over HTTPS "
                "on the tailnet, so no port is opened."))
    if _platform(setup) != "linux":
        setup.out(t("--server is for a Linux machine with no screen, and this one runs {platform}.",
                    platform=_platform(setup)))
        setup.out(t("Run 'nexgen openclaw setup' without --server to configure a desktop."))
        return True
    state = tailscale_state(setup.which("tailscale") or "", setup.run, setup.env)
    if not state.installed:
        setup.out(t("Tailscale is not installed, and this setup never installs a system program on its own."))
        setup.out(t("Install it from {url}, run 'tailscale up' and log in, then run this setup again.",
                    url=TAILSCALE_URL))
        return True
    if not state.connected:
        setup.out(t("Tailscale is installed but not connected ({state}): there is no address to publish the "
                    "gateway on.", state=state.state or t("no state reported")))
        setup.out(t("Run 'tailscale up' and log in, then run this setup again."))
        return True
    held = privileges(setup.which, setup.run, setup.env)
    if held.too_strong:
        _say_privileges(setup, held)
        if not yes_i_know:
            setup.out(t("Create the account the gateway will use without sudo and without the docker group, and run "
                        "the setup from there."))
            setup.out(t("If you accept the risk, run the same command again with --yes-i-know."))
            return True
        setup.out(t("--yes-i-know was given: the run goes on, and this line records what the gateway keeps."))
    setup.out(t("Tailscale is connected ({state}) and this account holds no root power: this machine can host "
                "the gateway.", state=state.state or t("running")))
    return False


def _say_privileges(setup: Setup, held: Privileges) -> None:
    """One line per power held, in the words of the power itself."""
    if held.root:
        setup.out(t("This account is root, and a gateway that runs agents must not run with root power."))
    if held.passwordless_sudo:
        setup.out(t("This account can become root without a password ('sudo -n true' succeeds), and a gateway "
                    "that runs agents must not carry that power."))
    if held.docker_group:
        setup.out(t("This account is in the 'docker' group, which is root through a container, and a gateway "
                    "that runs agents must not carry that power."))


def _verdict(severity: Severity, message: str, action: str | None = None) -> CheckOutcome:
    """One exposure line, in the one id the report groups it under."""
    return CheckOutcome(id="openclaw.gateway.exposure", severity=severity, message=message, action=action)


def exposure(config: dict[str, Any]) -> CheckOutcome:
    """How the gateway is reached, and a failure when a served one is also wide.

    The line exists because "the gateway is running" says nothing about who can
    reach it. A server gateway published through Tailscale Serve must stay on
    loopback: a second, wider listener is the port Serve was chosen to avoid.
    """
    gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
    bind = gateway.get("bind")
    tailscale = gateway.get("tailscale") if isinstance(gateway.get("tailscale"), dict) else {}
    mode = tailscale.get("mode")
    name = bind if isinstance(bind, str) and bind else t("(not set)")
    if mode == "serve":
        return _served(bind, name)
    if mode == "funnel":
        return _verdict(
            Severity.WARN,
            t("The gateway is published on the public internet through Tailscale Funnel, on {bind}.", bind=name),
            t("Use gateway.tailscale.mode 'serve' unless the public URL is what you want."),
        )
    return _listening(bind, name)


def _served(bind: Any, name: str) -> CheckOutcome:
    """A gateway published through Serve: safe only while it stays on loopback."""
    if bind in LOOPBACK_BINDS:
        return _verdict(
            Severity.OK,
            t("The gateway listens on loopback only and Tailscale Serve publishes it over HTTPS on the tailnet."),
        )
    return _verdict(
        Severity.BROKEN,
        t("A gateway published through Tailscale Serve must stay on loopback: this one also listens on {bind}, "
          "so the port is reachable without the HTTPS Serve provides.", bind=name),
        t("Set gateway.bind to 'loopback' and run 'nexgen openclaw setup --server' again."),
    )


def _listening(bind: Any, name: str) -> CheckOutcome:
    """A gateway with no Tailscale mode in front: where its listener stops."""
    if bind in LOOPBACK_BINDS:
        return _verdict(
            Severity.OK,
            t("The gateway listens on loopback only: nothing outside this machine can reach it."),
        )
    if bind == HOME_BIND:
        return _verdict(Severity.OK, t("The gateway listens on the home network ({bind}).", bind=name))
    if bind == TAILNET_BIND:
        return _verdict(
            Severity.WARN,
            t("The gateway listens on the Tailscale address itself ({bind}), with no HTTPS in front.", bind=name),
            t("Set gateway.bind to 'loopback' and gateway.tailscale.mode to 'serve' for an HTTPS address "
              "on the tailnet."),
        )
    return _verdict(
        Severity.WARN,
        t("The gateway listens on every interface ({bind}): the network it sits on decides who reaches it.",
          bind=name),
        t("Set gateway.bind to 'loopback', or to 'lan' when only the devices at home have to reach it."),
    )
