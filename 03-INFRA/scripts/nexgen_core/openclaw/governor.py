"""The owner's fallback chains in the shapes OpenClaw and the doctor read.

One job: take the chains `quota_policy` resolved and put them where OpenClaw looks
(`agents.entries.<id>.model.fallbacks`), say what the plugin's sidecar carries, and
judge in one line whether any agent is left without a chain. When to move a turn is
not decided here: OpenClaw does it on a provider failure, from its own file, and the
engine's plugin does it one step early, from the readings the beat wrote.

The sidecar also names the plan each ACP lane spends. On that lane the harness runs
as itself and OpenClaw's chains are never consulted, so there is nothing to move and
the honest answer is to refuse the turn: the plugin does it, from the same file and
the same reading, and only when the squad writes `acp_admission: true`.

Two rules are not preferences:

- a chain the owner wrote by hand is never removed. The proposal and the merge both
  put the person's entries first and only add what is missing;
- an empty policy writes nothing. Which providers to pay for and in which order is
  the owner's rule, and the engine has no default chain to fall back on.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.claude_cli import split_ref
from nexgen_core.quota_policy import Failover
from nexgen_core.report import CheckOutcome, Severity

#: The runtime block type that marks an agent as running on an ACP session
#: (`runtime.acp_runtime`), which is the lane this module can refuse a turn on.
ACP_RUNTIME = "acp"

#: The squad key that opts a machine into the admission branch. Off by default:
#: it stops a turn that would otherwise run.
ADMISSION_KEY = "acp_admission"


def admitted(squad: Mapping[str, Any] | None) -> bool:
    """Whether the squad asked for the ACP admission branch."""
    return isinstance(squad, Mapping) and squad.get(ADMISSION_KEY) is True


def agent_entries(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """The agents a configuration declares, or an empty mapping."""
    agents = config.get("agents")
    entries = agents.get("entries") if isinstance(agents, Mapping) else None
    return entries if isinstance(entries, Mapping) else {}


def with_chains(config: dict[str, Any], policy: Failover) -> dict[str, Any]:
    """The same proposal with `model.fallbacks` on every agent the policy covers.

    An agent's model becomes OpenClaw's object form only when it receives a chain:
    writing `fallbacks: []` would opt an agent out of the shared chain explicitly,
    which is the opposite of what an empty policy must do. A chain already on the
    entry is kept first, so a hand-written one is preserved and only extended.
    """
    if not policy.declared:
        return config
    entries = agent_entries(config)
    updated: dict[str, Any] = {}
    for agent_id, entry in entries.items():
        chain = policy.for_agent(agent_id)
        model = entry.get("model") if isinstance(entry, dict) else None
        primary = model.get("primary") if isinstance(model, dict) else model
        if not chain or not isinstance(primary, str) or not primary.strip():
            continue
        kept = [ref for ref in _refs(model) if ref not in chain]
        updated[agent_id] = {**entry, "model": {"primary": primary, "fallbacks": [*kept, *chain]}}
    if not updated:
        return config
    return {**config, "agents": {**config["agents"], "entries": {**entries, **updated}}}


def governor_section(policy: Failover, config: Mapping[str, Any]) -> dict[str, Any] | None:
    """What the plugin reads before every turn, or None when nothing is declared.

    Only the agents this proposal creates are listed: the plugin can then never
    offer a chain that OpenClaw's own file does not carry, which would move a turn
    early and then fail over somewhere else.
    """
    chains = policy.chains_for(agent_entries(config))
    if not chains:
        return None
    return {
        "block_at": policy.block_at,
        "state_max_age_seconds": policy.state_max_age_seconds,
        "agents": {agent_id: list(chain) for agent_id, chain in chains.items()},
    }


def acp_lanes(config: Mapping[str, Any]) -> dict[str, str]:
    """Every agent on an ACP lane, mapped to the plan its turn spends.

    The lane is read from `agents.entries.<id>.runtime.type`, which is the half
    `harness_lanes.py` writes; the plan is the provider of that agent's model,
    which is the same `provider` key `nexgen-quota-state.json` counts under. An
    agent whose model names no provider is left out: the plugin must never name a
    plan the state file cannot have a reading for.
    """
    lanes: dict[str, str] = {}
    for agent_id, entry in agent_entries(config).items():
        if not isinstance(entry, Mapping):
            continue
        runtime = entry.get("runtime")
        if not isinstance(runtime, Mapping) or runtime.get("type") != ACP_RUNTIME:
            continue
        model = entry.get("model")
        primary = model.get("primary") if isinstance(model, Mapping) else model
        provider, name = split_ref(primary if isinstance(primary, str) else "")
        if provider and name:
            lanes[str(agent_id)] = provider
    return lanes


def admission_section(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """The lanes the plugin may refuse a turn on, or None when no agent is on one."""
    lanes = acp_lanes(config)
    return {"agents": lanes} if lanes else None


def sidecar_section(
    policy: Failover, config: Mapping[str, Any], *, acp_admission: bool = False,
) -> dict[str, Any] | None:
    """The whole `governor` sidecar: the chains, the two numbers, and the ACP lanes.

    One section and one write, because the plugin replaces it whole. The admission
    branch is written only when the squad asks for it: refusing a turn on a lane
    whose plan reads spent is a real behaviour change, so it is the owner's switch
    and never a default.
    """
    section = governor_section(policy, config) or {}
    admission = admission_section(config) if acp_admission else None
    if admission:
        section = {**section, "acp": admission}
    if not section:
        return None
    section.setdefault("block_at", policy.block_at)
    section.setdefault("state_max_age_seconds", policy.state_max_age_seconds)
    return section


def agent_chains(config: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Every agent and the chain OpenClaw would use for it.

    An agent whose `model` is a plain string is strict: OpenClaw documents that it
    inherits nothing, so the shared `agents.defaults.model.fallbacks` does not reach
    it. Only an agent that names no model at all inherits the shared chain.
    """
    agents = config.get("agents") if isinstance(config.get("agents"), Mapping) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), Mapping) else {}
    shared = tuple(_refs(defaults.get("model")))
    chains: dict[str, tuple[str, ...]] = {}
    for agent_id, entry in agent_entries(config).items():
        model = entry.get("model") if isinstance(entry, Mapping) else entry
        if isinstance(model, Mapping):
            chains[str(agent_id)] = tuple(_refs(model))
        elif isinstance(model, str) and model.strip():
            chains[str(agent_id)] = ()
        else:
            chains[str(agent_id)] = shared
    return chains


def check_fallbacks(config: Mapping[str, Any], policy: Failover) -> CheckOutcome:
    """One line: the policy, how many agents carry a chain, and who carries none.

    A warning is reserved for the case the person actually asked for: the policy
    names an agent and that agent's chain is not in the configuration. A machine
    that declared nothing, and whose agents are strict, is a legitimate state and
    stays quiet: an agent that stops at a spent plan is a choice, not a fault.
    """
    chains = agent_chains(config)
    covered = sorted(agent for agent, chain in chains.items() if chain)
    missing = sorted(agent for agent, chain in chains.items() if not chain)
    named = [agent for agent in missing if agent in policy.agents]
    if not chains:
        return CheckOutcome(
            id="openclaw.fallbacks",
            severity=Severity.OK,
            message=t("No OpenClaw agent is configured yet: there is no fallback chain to judge."),
        )
    if named:
        return CheckOutcome(
            id="openclaw.fallbacks",
            severity=Severity.WARN,
            message=t("The fallback policy names these agents, and their chain is not in the configuration: {names}",
                      names=", ".join(named)),
            action=t("Run 'nexgen openclaw setup', and only for an agent the engine creates: it writes no agent it does not own."),
        )
    if missing:
        if not covered and not policy.declared:
            return CheckOutcome(
                id="openclaw.fallbacks",
                severity=Severity.OK,
                message=t("No fallback policy is declared, and no OpenClaw agent has a fallback chain."),
            )
        if policy.declared:
            message = t("The fallback policy is declared: {covered} agents have a chain, {missing} have none ({names}).",
                        covered=len(covered), missing=len(missing), names=", ".join(missing))
        else:
            message = t("No fallback policy is declared: {covered} agents have a chain of their own, "
                        "{missing} have none ({names}).",
                        covered=len(covered), missing=len(missing), names=", ".join(missing))
        return CheckOutcome(id="openclaw.fallbacks", severity=Severity.OK, message=message)
    message = (
        t("The fallback policy is declared: every one of the {count} agents has a chain.", count=len(covered))
        if policy.declared else
        t("No fallback policy is declared; the {count} agents have a chain of their own.", count=len(covered))
    )
    return CheckOutcome(id="openclaw.fallbacks", severity=Severity.OK, message=message)


def _refs(model: Any) -> list[str]:
    """The model refs a `model` value names as fallbacks, tolerating both shapes."""
    if not isinstance(model, Mapping):
        return []
    chain = model.get("fallbacks")
    if not isinstance(chain, list):
        return []
    return [ref for ref in chain if isinstance(ref, str) and ref.strip()]
