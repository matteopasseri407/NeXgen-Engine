"""What the gateway knows about its nodes, the network they would need, and the browser they drive.

One job: the doctor's lines about a machine in a fleet — which nodes are paired
and how they are doing, whether another PC could reach this gateway at all, and
whether the browser profile in the file is one OpenClaw can only attach to. They
were part of `doctor_runtime.py` until that file reached its size ceiling; they
are here because the questions are about the fleet rather than about what runs on
this machine.

The node list is the one thing here that costs a run of OpenClaw's CLI, so it is
asked only by a caller that asked for it, and never by the doctor on its own
schedule. The network line and the browser line are read from the configuration
and need no query.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.browser_profile import attach_only_missing
from nexgen_core.openclaw.doctor_runtime import DEFAULT_GATEWAY_PORT, LISTEN_TIMEOUT_SECONDS, Connect
from nexgen_core.report import CheckOutcome, Severity

#: `() -> list of nodes or None`: the gateway asked for its nodes, replaceable in tests.
NodeLister = Callable[[], "list[dict[str, Any]] | None"]


def default_node_lister() -> list[dict[str, Any]] | None:
    """The nodes the gateway knows, or None when it does not answer.

    The query is the read-only one OpenClaw documents, with the deadline
    `openclaw_pairing` puts on it, so a wedged gateway costs one silent line and no
    more. Silence is the honest answer here: a gateway that is down knows nothing,
    and the machine is not broken for it.
    """
    from nexgen_core.openclaw_pairing import nodes_status

    listing = nodes_status()
    if not listing.get("ok"):
        return None
    return listing.get("nodes") or []


def _is_connected(node: dict[str, Any]) -> bool:
    """Whether a node is up, in either of the two shapes the payload arrives in.

    The gateway's own list carries a boolean `connected`; a CLI payload may carry
    `status: "connected"` instead, and reading only the boolean would call a live
    node offline, which is a lie a person would act on.
    """
    return bool(node.get("connected")) or str(node.get("status") or "").strip().lower() == "connected"


def _node_detail(node: dict[str, Any]) -> str:
    """One node as a person reads it: which machine, on what, and whether it is up."""
    label = node.get("displayName") or node.get("nodeId") or "?"
    platform = node.get("platform") if isinstance(node.get("platform"), str) else ""
    state = t("connected") if _is_connected(node) else t("not connected")
    return f"{label} ({platform}, {state})" if platform else f"{label} ({state})"


def _nodes_outcome(nodes: list[dict[str, Any]]) -> CheckOutcome:
    """The paired nodes, or the plain fact that there are none."""
    if not nodes:
        return CheckOutcome(
            id="openclaw.nodes",
            severity=Severity.OK,
            message=t("No node is paired with this gateway."),
        )
    items = "; ".join(_node_detail(node) for node in nodes)
    return CheckOutcome(
        id="openclaw.nodes",
        severity=Severity.OK,
        message=t("Nodes this gateway knows: {items}", items=items),
    )


def _lan_outcome(bind: Any, *, node_known: bool, served: bool = False) -> CheckOutcome:
    """A node on another PC needs the gateway to answer past this machine.

    The fact is in the configuration, so the line is always there: a person who is
    about to connect a second machine has to learn it before the attempt, not after.
    It is a warning only when a node is actually known to exist and is therefore
    unable to connect; on a single-PC install the same sentence is information, and
    on a laptop outside the house loopback is the only thing that works.

    A gateway published through Tailscale Serve is the one case where loopback is
    not an obstacle: the node reaches it by the gateway's MagicDNS name, and
    telling the person to open the LAN would reopen exactly the port Serve avoids.
    """
    if served:
        return CheckOutcome(
            id="openclaw.gateway.lan",
            severity=Severity.OK,
            message=t("The gateway is published through Tailscale Serve: a node on another PC reaches it by the "
                      "gateway's MagicDNS name, and no port is opened here."),
            action=t("Run 'nexgen openclaw node --host <host>.<tailnet>.ts.net' on the other machine."),
        )
    if bind == "lan":
        return CheckOutcome(
            id="openclaw.gateway.lan",
            severity=Severity.OK,
            message=t("The gateway listens on the home network: a node on another PC can reach it."),
        )
    return CheckOutcome(
        id="openclaw.gateway.lan",
        severity=Severity.WARN if node_known else Severity.OK,
        message=t("The gateway listens on this machine only ({bind}): a node on another PC cannot reach it.",
                  bind=bind if isinstance(bind, str) and bind else t("(not set)")),
        action=t("Run 'nexgen openclaw setup --lan' here and restart the gateway, when a second machine has to reach it."),
    )


def _quoted(names: tuple[str, ...]) -> str:
    """The profile names as a person reads them, each one in quotes."""
    return ", ".join(f"'{name}'" for name in names)


def check_browser_attach(config: dict[str, Any]) -> CheckOutcome | None:
    """A profile pointed at a browser on this machine must say it only attaches.

    OpenClaw reads a loopback `cdpUrl` as a profile it manages, so without
    `attachOnly` it tries to own a port it did not open and reports port
    ownership errors: on a node that port belongs to the engine's shared Chrome,
    which starts browsers of its own on purpose. Nothing is said when no profile
    names a loopback port, because then there is nothing to fix.
    """
    missing = attach_only_missing(config)
    if not missing:
        return None
    return CheckOutcome(
        id="openclaw.browser.attach",
        severity=Severity.WARN,
        message=t("The browser profile {names} attaches to a browser on this machine through a loopback port "
                  "without 'attachOnly': OpenClaw reads that port as a profile it manages and reports port "
                  "ownership errors.", names=_quoted(missing)),
        action=t("Add 'attachOnly': true to browser.profiles.{names} in openclaw.json.",
                 names=missing[0]),
    )


def check_nodes(
    config: dict[str, Any],
    connect: Connect,
    lister: NodeLister | None = None,
) -> list[CheckOutcome]:
    """The nodes the gateway knows, and the network a node would need to reach it.

    `lister` is what makes the first line possible, and it is never installed by
    default: asking the gateway means starting OpenClaw's CLI, and `nexgen doctor`
    runs on its own schedule. A caller that wants the node list says so.
    """
    gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
    port = gateway.get("port") if isinstance(gateway.get("port"), int) else DEFAULT_GATEWAY_PORT
    tailscale = gateway.get("tailscale") if isinstance(gateway.get("tailscale"), dict) else {}
    served = tailscale.get("mode") == "serve"
    nodes: list[dict[str, Any]] | None = None
    if lister is not None and connect("127.0.0.1", int(port), LISTEN_TIMEOUT_SECONDS):
        nodes = lister()
    outcomes: list[CheckOutcome] = []
    if nodes is not None:
        outcomes.append(_nodes_outcome(nodes))
    outcomes.append(_lan_outcome(gateway.get("bind"), node_known=bool(nodes), served=served))
    return outcomes
