"""Whether the PC an agent runs its commands on will stop them.

One job: read a node's exec approvals, in read-only mode, and say whether that PC
ends up stricter than the policy the agent asks for. It is the failure a node
gives when nothing can approve a command there: `exec denied: request-failed`,
raised by a prompt on a machine with no screen whose fallback is `deny`.

The merge is OpenClaw's, not this module's. `openclaw approvals get --node <id>
--json` answers with the requested `tools.exec` policy, the host approvals-file
policy and the merged `effectivePolicy` for every agent scope (docs.openclaw.ai/
cli/approvals, "`get` shows the effective exec policy for the target"), so the
judgement here is `effective` against `requested` per field: a field the node did
not tighten reads the same and says nothing, and the precedence OpenClaw applies
to `file.agents`/`file.defaults` is no longer re-derived in Python.

Two shapes still arrive from that command, and both are read here: the merged
policy above, and the host-native policy of the Windows and macOS companions
(`enabled`, `defaultAction`, `rules`), which OpenClaw reports as it is because it
cannot configure it from the gateway at all.

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

#: The values OpenClaw accepts for each policy field. A field outside these is not
#: a policy this check can judge, and comparing it would invent a verdict.
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
        outcome = _agent_outcome(agent_id, node, readings[node])
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def _exec_block(tools: Any) -> dict[str, Any]:
    """The `exec` block of one tools section, or an empty one."""
    block = tools.get("exec") if isinstance(tools, dict) else None
    return block if isinstance(block, dict) else {}


def _scopes(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The merged per-agent policies the native answer carries, and none when it computed none."""
    policy = document.get("effectivePolicy")
    scopes = policy.get("scopes") if isinstance(policy, dict) else None
    if not isinstance(scopes, list):
        return []
    return [scope for scope in scopes if isinstance(scope, dict)]


def _scope_for(document: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
    """The scope of that one agent, or None when the answer merged nothing for it.

    An older node reports its effective policy as unavailable instead of assuming
    the gateway's request also applies on its host, and a node that has not answered
    the merge at all carries no scope: both are read as "not judged", never as
    agreement.
    """
    for scope in _scopes(document):
        if scope.get("agentId") == agent_id:
            return scope
    return None


def _text(block: Any, key: str) -> str:
    """One field of a native policy block, or "" when it is absent or not a string."""
    value = block.get(key) if isinstance(block, dict) else None
    return value.strip().lower() if isinstance(value, str) else ""


def _differences(scope: dict[str, Any]) -> list[str]:
    """The fields where the node ends up stricter than the agent asks for.

    `effective` is the intersection OpenClaw computed, so a field it did not
    tighten equals the request, and a field holding a value OpenClaw would refuse
    is skipped rather than compared.
    """
    found: list[str] = []
    for field, accepted in (("security", SECURITY), ("ask", ASK)):
        asked, effective = _text(scope.get(field), "requested"), _text(scope.get(field), "effective")
        if asked not in accepted or effective not in accepted or effective == asked:
            continue
        found.append(t("{field} {node} instead of {asked}", field=field, node=effective, asked=asked))
    if _a_question_is_raised(scope):
        fallback = _text(scope.get("askFallback"), "effective")
        if fallback in ASK_FALLBACK and fallback != "full":
            found.append(t("askFallback {value}: a question raised there is answered by nobody, and the run is "
                           "denied", value=fallback))
    return found


def _a_question_is_raised(scope: dict[str, Any]) -> bool:
    """Whether this PC would ever ask, which is what gives its fallback something to answer.

    `off` never asks. Omitted, the fallback is `deny`: the documented default, and
    the one that turns a prompt into a refusal.
    """
    return _text(scope.get("ask"), "effective") in ("on-miss", "always")


def _agent_outcome(agent_id: str, node: str, reading: Reading) -> CheckOutcome | None:
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
    scope = _scope_for(document, agent_id)
    if scope is None:
        return None
    differences = _differences(scope)
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
