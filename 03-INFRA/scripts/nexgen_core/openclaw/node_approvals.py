"""Whether the PC an agent runs its commands on will stop them.

One job: read a node's exec approvals, in read-only mode, and say whether that PC
is stricter than the policy the agent asks for. It is the failure a node gives
when nothing can approve a command there: `exec denied: request-failed`, raised by
a prompt on a machine with no screen whose fallback is `deny`.

Two shapes arrive from the same documented command, and both are read here: the
approvals document OpenClaw keeps on a headless node host, whose fields are the
policy (`security`, `ask`, `askFallback`), and the host-native policy of the
Windows and macOS companions (`enabled`, `defaultAction`, `rules`), which
OpenClaw cannot configure from the gateway at all.

Nothing here writes. The approvals belong to the node's own host and are the
person's; the fix is the command the check prints, and the same body is what
`install.sh --role node --exec-policy full` applies when that is what was asked.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity

#: Each policy field, loosest first: a value further right stops more commands.
SECURITY = ("full", "allowlist", "deny")
ASK = ("off", "on-miss", "always")
ASK_FALLBACK = ("full", "allowlist", "deny")

#: The "never prompt" body of OpenClaw's documentation (`docs/cli/approvals.md`):
#: full security, no question, and a fallback that still allows the run. Written
#: once, because the doctor prints it and the node role applies it.
NEVER_PROMPT_BODY = '{"version": 1, "defaults": {"security": "full", "ask": "off", "askFallback": "full"}}'

#: The host-native body that allows every command. `rules` is required because the
#: command replaces the node's complete rule list.
NATIVE_ALLOW_BODY = '{"defaultAction": "allow", "rules": []}'


@dataclass(frozen=True)
class Reading:
    """What one node answered: its approvals document, or why there is none."""

    document: dict[str, Any] | None = None
    reason: str = ""


#: `(node) -> Reading`: the node's approvals, replaceable in tests.
Reader = Callable[[str], Reading]


def default_reader(node: str) -> Reading:
    """`openclaw approvals get --node <node> --json`, with the deadline the pairing queries use.

    The command is the read-only one OpenClaw documents for a node target, and its
    answer is never printed here: only the fields this module judges are read.
    """
    from nexgen_core.openclaw_pairing import default_node_runner

    res = default_node_runner(["openclaw", "approvals", "get", "--node", node, "--json"])
    if res.returncode != 0:
        said = (res.stderr or res.stdout).strip().splitlines()
        return Reading(reason=said[-1] if said else t("the command exited with {code}", code=res.returncode))
    try:
        document = json.loads(res.stdout or "")
    except ValueError:  # nexgen: allow reason="an answer that is not JSON is reported as unreadable, never guessed at"
        return Reading(reason=t("the answer is not JSON"))
    if not isinstance(document, dict):
        return Reading(reason=t("the answer is not an approvals document"))
    return Reading(document=document)


def node_agents(config: dict[str, Any]) -> dict[str, str]:
    """agent id -> the node its commands run on, for the agents that ask for one.

    `tools.exec.host` and `.node` are read per agent over the shared block, because
    that is how OpenClaw resolves them. An agent that names no node is left out: it
    may legally run on whichever node is the only one eligible, and this check has
    no node list to resolve that with.
    """
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    shared = _exec_block(config.get("tools"))
    targets: dict[str, str] = {}
    for agent_id, entry in entries.items():
        own = _exec_block(entry.get("tools") if isinstance(entry, dict) else None)
        host = own.get("host") if own.get("host") is not None else shared.get("host")
        node = own.get("node") if own.get("node") is not None else shared.get("node")
        if host == "node" and isinstance(node, str) and node.strip():
            targets[str(agent_id)] = node.strip()
    return targets


def check_agents(config: dict[str, Any], reader: Reader | None = None) -> list[CheckOutcome]:
    """One line per agent whose commands run on a node, or nothing when none does.

    The reader is what makes the lines possible: asking a node starts OpenClaw's
    CLI, so `nexgen doctor` on its own schedule passes none and never asks. A node
    read once answers for every agent that runs on it.
    """
    targets = node_agents(config)
    if reader is None or not targets:
        return []
    readings: dict[str, Reading] = {}
    outcomes: list[CheckOutcome] = []
    for agent_id, node in targets.items():
        if node not in readings:
            readings[node] = reader(node)
        outcome = _agent_outcome(config, agent_id, node, readings[node])
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def _exec_block(tools: Any) -> dict[str, Any]:
    """The `exec` block of one tools section, or an empty one."""
    block = tools.get("exec") if isinstance(tools, dict) else None
    return block if isinstance(block, dict) else {}


def _requested_policy(config: dict[str, Any], agent_id: str) -> dict[str, str] | None:
    """What the agent asks for: its own `tools.exec`, over the shared one.

    OpenClaw fills a gap with its own `full` and `off`, which are also this check's
    defaults. A policy written as `tools.exec.mode` is not interpreted: the check
    says nothing rather than compare against a shape it would have to guess at.
    """
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    entry = entries.get(agent_id) if isinstance(entries.get(agent_id), dict) else {}
    own = _exec_block(entry.get("tools"))
    shared = _exec_block(config.get("tools"))
    if (own.get("mode") or shared.get("mode")) and not (own.get("security") or own.get("ask")):
        return None
    policy = {
        "security": str(own.get("security") or shared.get("security") or "full"),
        "ask": str(own.get("ask") or shared.get("ask") or "off"),
    }
    fallback = own.get("askFallback") or shared.get("askFallback")
    if isinstance(fallback, str) and fallback.strip():
        policy["askFallback"] = fallback.strip()
    return policy


def _host_policy(document: dict[str, Any], agent_id: str) -> dict[str, str]:
    """The node's policy for one agent: its own entry, then its wildcard, then its defaults.

    That is the precedence OpenClaw resolves (`agents.<id>` wins a field over
    `agents."*"`, which wins over `defaults`), and a field set nowhere is left out:
    OpenClaw then uses the value the agent asks for.
    """
    file = document.get("file") if isinstance(document.get("file"), dict) else {}
    agents = file.get("agents") if isinstance(file.get("agents"), dict) else {}
    defaults = file.get("defaults") if isinstance(file.get("defaults"), dict) else {}
    layers = [agents.get(agent_id), agents.get("*"), defaults]
    policy: dict[str, str] = {}
    for field in ("security", "ask", "askFallback"):
        for layer in layers:
            value = layer.get(field) if isinstance(layer, dict) else None
            if isinstance(value, str) and value.strip():
                policy[field] = value.strip()
                break
    return policy


def _differences(requested: dict[str, str], host: dict[str, str]) -> list[str]:
    """The fields where the node stops what the agent asks for, in the order a person reads them."""
    found: list[str] = []
    for field, order in (("security", SECURITY), ("ask", ASK)):
        asked, theirs = requested.get(field, ""), host.get(field, "")
        mine, node = _rank(order, asked), _rank(order, theirs)
        if mine is None or node is None or node <= mine:
            continue
        found.append(t("{field} {node} instead of {asked}", field=field, node=theirs, asked=asked))
    if _fallback_denies(requested, host):
        found.append(t("askFallback {value}: a question raised there is answered by nobody, and the run is denied",
                       value=host.get("askFallback") or "deny"))
    return found


def _fallback_denies(requested: dict[str, str], host: dict[str, str]) -> bool:
    """Whether a question the node would raise ends in a denial nobody chose.

    A question is raised when the stricter of the two `ask` values is not `off`, and
    it is answered by the node's fallback. Omitted, that fallback is `deny`: the
    documented default, and the one that turns a prompt into a refusal.
    """
    if _further(ASK, requested.get("ask"), host.get("ask")) in (None, "off"):
        return False
    their_rank = _rank(ASK_FALLBACK, host.get("askFallback") or "deny") or 0
    asked_rank = _rank(ASK_FALLBACK, requested.get("askFallback") or "full") or 0
    return their_rank > asked_rank


def _further(order: tuple[str, ...], left: Any, right: Any) -> str | None:
    """The stricter of two values of one field, or None when neither is one OpenClaw accepts."""
    ranks = [(rank, value) for value in (left, right) if (rank := _rank(order, value)) is not None]
    return max(ranks)[1] if ranks else None


def _rank(order: tuple[str, ...], value: Any) -> int | None:
    """Where a value sits in its order, or None when it is not one the field accepts."""
    if not isinstance(value, str):
        return None
    try:
        return order.index(value.strip().lower())
    except ValueError:  # nexgen: allow reason="a value OpenClaw would refuse is not a policy this check can judge"
        return None


def _agent_outcome(config: dict[str, Any], agent_id: str, node: str, reading: Reading) -> CheckOutcome | None:
    """The line for one agent, or None when its policy cannot be judged."""
    check_id = f"openclaw.nodes.exec.{agent_id}"
    if reading.document is None:
        return CheckOutcome(
            id=check_id,
            severity=Severity.WARN,
            message=t("Agent '{agent}' runs its commands on node '{node}', and that node's approvals cannot be read: "
                      "{reason}", agent=agent_id, node=node, reason=reading.reason or t("no answer")),
            action=t("Run 'openclaw approvals get --node {node}' to see what the gateway says, then "
                     "'nexgen openclaw doctor --nodes' again.", node=node),
        )
    document = reading.document
    if "file" not in document:
        return _native_outcome(check_id, agent_id, node, document)
    requested = _requested_policy(config, agent_id)
    if requested is None:
        return None
    differences = _differences(requested, _host_policy(document, agent_id))
    if not differences:
        return CheckOutcome(
            id=check_id,
            severity=Severity.OK,
            message=t("Agent '{agent}' runs its commands on node '{node}', and that node accepts what the agent asks "
                      "for.", agent=agent_id, node=node),
        )
    return CheckOutcome(
        id=check_id,
        severity=Severity.WARN,
        message=t("Agent '{agent}' runs its commands on node '{node}', and that node stops some of them: {detail}.",
                  agent=agent_id, node=node, detail="; ".join(differences)),
        action=t("On that PC run 'openclaw approvals set --stdin', or from here 'openclaw approvals set --node "
                 "{node} --stdin', with the body OpenClaw documents for a host that never stops on an approval: "
                 "{body}", node=node, body=NEVER_PROMPT_BODY),
    )


def _native_outcome(check_id: str, agent_id: str, node: str, document: dict[str, Any]) -> CheckOutcome:
    """A companion that enforces its own policy: OpenClaw cannot write it from the gateway."""
    if document.get("enabled") is False:
        return CheckOutcome(
            id=check_id,
            severity=Severity.WARN,
            message=t("Agent '{agent}' runs its commands on node '{node}', which reports its own exec approvals as "
                      "disabled: OpenClaw cannot configure that policy from here.", agent=agent_id, node=node),
            action=t("On that PC, enable its exec approvals and choose the policy it should enforce."),
        )
    action = document.get("defaultAction")
    if action == "allow":
        return CheckOutcome(
            id=check_id,
            severity=Severity.OK,
            message=t("Agent '{agent}' runs its commands on node '{node}', which enforces its own policy and allows "
                      "them by default.", agent=agent_id, node=node),
        )
    return CheckOutcome(
        id=check_id,
        severity=Severity.WARN,
        message=t("Agent '{agent}' runs its commands on node '{node}', which enforces its own exec policy ({action}): "
                  "OpenClaw cannot write it from here, so the agent's commands stop on that PC.",
                  agent=agent_id, node=node, action=action if isinstance(action, str) and action else t("(not set)")),
        action=t("From here, 'openclaw approvals set --node {node} --stdin' with {body} replaces that PC's whole rule "
                 "list with one that allows every command.", node=node, body=NATIVE_ALLOW_BODY),
    )
