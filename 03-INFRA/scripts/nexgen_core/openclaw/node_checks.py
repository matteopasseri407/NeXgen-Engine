"""What the gateway knows about its nodes, and the network they would need.

One job: the doctor's two lines about nodes — which machines are paired and how they
are doing, and whether another PC could reach this gateway at all. They were part of
`doctor_runtime.py` until that file reached its size ceiling; they are here because the
question is about the network rather than about what runs on this machine.

The node list is the one thing here that costs a run of OpenClaw's CLI, so it is
asked only by a caller that asked for it, and never by the doctor on its own
schedule. The network line is read from the configuration and needs no query.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nexgen_core.i18n import t
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


def _node_detail(node: dict[str, Any]) -> str:
    """One node as a person reads it: which machine, on what, and whether it is up."""
    label = node.get("displayName") or node.get("nodeId") or "?"
    platform = node.get("platform") if isinstance(node.get("platform"), str) else ""
    state = t("connected") if node.get("connected") else t("not connected")
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


def _lan_outcome(bind: Any, *, node_known: bool) -> CheckOutcome:
    """A node on another PC needs the gateway to answer past this machine.

    The fact is in the configuration, so the line is always there: a person who is
    about to connect a second machine has to learn it before the attempt, not after.
    It is a warning only when a node is actually known to exist and is therefore
    unable to connect; on a single-PC install the same sentence is information, and
    on a laptop outside the house loopback is the only thing that works.
    """
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
    nodes: list[dict[str, Any]] | None = None
    if lister is not None and connect("127.0.0.1", int(port), LISTEN_TIMEOUT_SECONDS):
        nodes = lister()
    outcomes: list[CheckOutcome] = []
    if nodes is not None:
        outcomes.append(_nodes_outcome(nodes))
    outcomes.append(_lan_outcome(gateway.get("bind"), node_known=bool(nodes)))
    return outcomes
