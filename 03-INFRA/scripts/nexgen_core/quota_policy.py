"""The chains a plan hands over to, as the owner declares them, never as a default.

One job: turn the optional `failover:` block of the Vault's `quota.yaml` into the
ordered chains an agent receives and the two numbers the handover obeys. Which
providers to pay for and in which order is the owner's rule, so a fresh install
declares nothing: an empty policy writes no fallback anywhere and leaves every
chain the owner wrote by hand in OpenClaw exactly as it is.

Nothing here runs a turn. The chains reach OpenClaw through its own file
(`agents.entries.<id>.model.fallbacks`), where OpenClaw does the switching on a
provider failure, and they reach the engine's plugin through the sidecar beside
it, where a turn moves one step earlier. This module owns only the vocabulary the
person writes, so a malformed line costs that line and never the command.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from nexgen_core.i18n import t

#: The line above which a spent plan is a reason to move the turn, and the age past
#: which a reading is not trusted. Both are the policy's to change; these are the
#: shipped ones. `DEFAULT_BLOCK_AT` is deliberately the urgent alert band: the plan
#: that raises the loudest alert is the plan about to stop answering.
DEFAULT_BLOCK_AT = 95.0
DEFAULT_STATE_MAX_AGE_SECONDS = 3 * 3600

#: A model as OpenClaw names it: `provider/model`, no spaces. Anything else is not a
#: ref, and writing one would make the whole configuration invalid at the next start.
SEPARATOR = "/"


@dataclass(frozen=True)
class Failover:
    """What the owner declared: the chains, and the two numbers the switch obeys."""

    block_at: float = DEFAULT_BLOCK_AT
    state_max_age_seconds: int = DEFAULT_STATE_MAX_AGE_SECONDS
    #: For every agent that has no entry of its own.
    chain: tuple[str, ...] = ()
    #: Per agent, in OpenClaw's own ids.
    agents: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def declared(self) -> bool:
        """Whether the owner asked for any chain at all."""
        return bool(self.chain) or bool(self.agents)

    def for_agent(self, agent_id: str | None) -> tuple[str, ...]:
        """What this agent falls back to: its own chain, else the shared one."""
        return tuple(self.agents.get(str(agent_id)) or self.chain)

    def chains_for(self, agent_ids: Iterable[str]) -> dict[str, tuple[str, ...]]:
        """The agents that receive something, in the order they were asked for."""
        return {agent: chain for agent in (str(item) for item in agent_ids)
                if (chain := self.for_agent(agent))}

    def to_dict(self) -> dict[str, Any]:
        """The policy as JSON data, for `nexgen openclaw plan --json`."""
        return {
            "block_at": self.block_at,
            "state_max_age_seconds": self.state_max_age_seconds,
            "chain": list(self.chain),
            "agents": {agent: list(chain) for agent, chain in self.agents.items()},
        }


def path(home: Path | None = None, *, vault: Path | None = None) -> Path:
    """The file the ceilings already live in: one file, two sections.

    The location is the report's decision and is asked for here rather than copied,
    because the import has to stay inside the function: `quota` reads this module to
    print what a row hands over to, and a module-level import would close the circle.
    """
    from nexgen_core import quota

    return quota.limits_path(home, vault=vault)


def load(home: Path | None = None, *, vault: Path | None = None) -> tuple[Failover, list[str]]:
    """The policy this machine declares, and what could not be used."""
    return read(path(home, vault=vault))


def read(target: Path) -> tuple[Failover, list[str]]:
    """The `failover` section of `target`, or an empty policy and the reason.

    The file is optional and so is the section: a machine that declares nothing is
    the normal state, not a fault, and it costs no line anywhere.
    """
    if not target.is_file():
        return Failover(), []
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return Failover(), [t("The fallback policy in {path} cannot be read: {error}", path=target, error=exc)]
    declared = data.get("failover") if isinstance(data, dict) else None
    if not isinstance(declared, dict):
        return Failover(), []

    notes: list[str] = []
    chain, bad = _chain(declared.get("chain"))
    notes.extend(_unusable(bad))
    agents: dict[str, tuple[str, ...]] = {}
    declared_agents = declared.get("agents")
    if isinstance(declared_agents, dict):
        for agent_id, value in declared_agents.items():
            agent_chain, bad = _chain(value)
            notes.extend(_unusable(bad))
            if agent_chain:
                agents[str(agent_id)] = agent_chain
    elif declared_agents is not None:
        notes.append(t("The fallback 'agents' entry is not a mapping of agent to chain: ignored."))
    return Failover(
        block_at=_block_at(declared.get("block_at"), notes),
        state_max_age_seconds=_max_age(declared.get("state_max_age_minutes"), notes),
        chain=chain,
        agents=agents,
    ), notes


def provider_of(ref: str) -> str:
    """The provider a model ref names, or "" when the ref names none."""
    cut = ref.find(SEPARATOR)
    return ref[:cut] if cut > 0 else ""


def handover_to(chain: Sequence[str], provider_id: str) -> str:
    """The first model in a chain that does not run on this same provider.

    A plan that is spent takes its own sibling models with it, so what a person
    needs to read next to a provider is the model that runs somewhere else. A chain
    that names only this provider still shows its head: it is what a turn would try,
    and saying nothing would claim the policy promises no fallback at all.
    """
    for ref in chain:
        if not provider_id or provider_of(ref) != provider_id:
            return ref
    return chain[0] if chain else ""


def _chain(value: Any) -> tuple[tuple[str, ...], list[str]]:
    """The model refs a chain names, and the entries that are not one."""
    items = value if isinstance(value, list) else [value]
    refs: list[str] = []
    bad: list[str] = []
    for item in items:
        text = item.strip() if isinstance(item, str) else ""
        if text and SEPARATOR in text and not any(char.isspace() for char in text):
            if text not in refs:
                refs.append(text)
        elif item is not None:
            bad.append(str(item))
    return tuple(refs), bad


def _unusable(bad: list[str]) -> list[str]:
    """One line per entry that is not a model ref, so a typo is never silent."""
    return [
        t("The fallback model {entry} is not written as 'provider/model': ignored.", entry=entry)
        for entry in bad
    ]


def _block_at(value: Any, notes: list[str]) -> float:
    """The percentage above which a turn moves, or the shipped one."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < float(value) <= 100:
        return float(value)
    if value is not None:
        notes.append(t(
            "The fallback line {value} is not a percentage between 0 and 100: {default} is used.",
            value=value, default=f"{DEFAULT_BLOCK_AT:.0f}",
        ))
    return DEFAULT_BLOCK_AT


def _max_age(value: Any, notes: list[str]) -> int:
    """How long a written reading stays usable, in seconds, or the shipped three hours."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) > 0:
        return int(float(value) * 60)
    if value is not None:
        notes.append(t(
            "The fallback reading age {value} is not a positive number of minutes: {default} hours are used.",
            value=value, default=f"{DEFAULT_STATE_MAX_AGE_SECONDS // 3600}",
        ))
    return DEFAULT_STATE_MAX_AGE_SECONDS
