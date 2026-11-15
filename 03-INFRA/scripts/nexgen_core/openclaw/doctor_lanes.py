"""Which lane each configured OpenClaw agent really runs on, and whether its ACP half is there.

nexgen: one-job reason="name each agent's lane and flag an ACP runtime with no binding behind it"

One job: read the configuration in force and say, per agent, which of the three lanes
runs its turn -- an ACP session, a CLI backend, or a direct provider -- and flag the two
ways that answer can be wrong: an agent a person talks to spending a paid plan through
OpenClaw's own prompt, and an ACP runtime block with no binding behind it.

Why it exists. `harness_lanes.py` decides the lane a squad asks for; nothing said which
lane a machine is actually on. The engine's own `docs/proposals/openclaw-thin-wrapper.md`
found three agents described as harness agents while they answered through a provider
API, and `docs/proposals/daru-burn.md` measured what the one real CLI lane costs. A
person looking at a bill needs that line before the bill.

What a lane means, in the pinned release:

- `acp`: the agent's entry carries `runtime.type: "acp"`, and a `bindings[]` entry of
  type `acp` binds a conversation to it (`tools/acp-agents/quickstart`). The harness runs
  as itself, in the `cwd` the runtime block names, on its own login and plan.
- `cli`: a model runtime is pinned for the agent's model (`agents.entries.<id>.models` or
  `agents.defaults.models[ref].agentRuntime`), or the model names the `agy` plugin
  provider. The official client runs the turn and OpenClaw keeps its own prompt.
- `provider`: neither. OpenClaw calls the model API itself with the key it holds, on its
  own prompt, and pays that prompt on every model call.

The warning is about a paid plan, not about taste. An agent no channel routes to is
nobody's cost; an agent on a local model costs nothing per token; an agent on `agy` is a
CLI backend already. What is left -- a bound agent on a provider lane -- is the case the
proposal names: the flat plan is spent by a client the owner does not see.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.claude_cli import agent_ref, runtime_id, split_ref
from nexgen_core.openclaw.local_models import LOCAL_PROVIDERS
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND
from nexgen_core.report import CheckOutcome, Severity

#: The three lanes an agent's turn can run on, as the configuration decides them.
ACP = "acp"
CLI = "cli"
PROVIDER = "provider"

#: The doctor check identifiers.
LANE_CHECK_ID = "openclaw.harness_lane"
BINDING_CHECK_ID = "openclaw.acp_binding"


@dataclass(frozen=True)
class LaneReading:
    """One agent's lane: what runs its turn and, when there is one, which runtime."""

    agent: str
    kind: str
    runtime: str = ""
    model: str = ""

    @property
    def paid(self) -> bool:
        """Whether this lane's turns are charged to a plan rather than to nothing at all."""
        provider, _ = split_ref(self.model)
        return self.kind == PROVIDER and bool(provider) and provider not in LOCAL_PROVIDERS


def _dict(value: Any) -> dict[str, Any]:
    """The value when it is a dict, an empty one otherwise: a config is data."""
    return value if isinstance(value, dict) else {}


def _entries(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """The `agents.entries` map, as written in the file."""
    agents = _dict((config or {}).get("agents"))
    return _dict(agents.get("entries"))


def lane_of(config: Mapping[str, Any] | None, agent_id: str) -> LaneReading:
    """The lane one agent's turn runs on, read from the configuration in force."""
    entry = _dict(_entries(config).get(agent_id))
    model = agent_ref(_dict(config), str(agent_id)) or ""
    runtime = _dict(entry.get("runtime"))
    if str(runtime.get("type") or "").strip().lower() == ACP:
        return LaneReading(str(agent_id), ACP, str(_dict(runtime.get("acp")).get("agent") or ""), model)
    pinned = runtime_id(_dict(config), str(agent_id), model) or ""
    if pinned:
        return LaneReading(str(agent_id), CLI, pinned, model)
    provider, _ = split_ref(model)
    if provider == LOCAL_AGY_BACKEND:
        return LaneReading(str(agent_id), CLI, LOCAL_AGY_BACKEND, model)
    return LaneReading(str(agent_id), PROVIDER, "", model)


def lanes(config: Mapping[str, Any] | None) -> list[LaneReading]:
    """Every configured agent's lane, in the file's order."""
    return [lane_of(config, str(agent_id)) for agent_id in _entries(config)]


def routed_agents(config: Mapping[str, Any] | None) -> set[str]:
    """The agents some binding routes a conversation to: the ones a person talks to."""
    bindings = (config or {}).get("bindings")
    if not isinstance(bindings, list):
        return set()
    return {str(one.get("agentId")) for one in bindings if isinstance(one, Mapping)}


def _summary(readings: list[LaneReading]) -> str:
    """One agent per item, `agent: lane (runtime)`, as the doctor line reads it."""
    parts: list[str] = []
    for reading in readings:
        parts.append(f"{reading.agent}: {reading.kind}" + (f" ({reading.runtime})" if reading.runtime else ""))
    return ", ".join(parts)


def check_harness_lane(config: dict[str, Any]) -> CheckOutcome | None:
    """Name every agent's lane, and warn for a bound agent whose turn is a provider call."""
    readings = lanes(config)
    if not readings:
        return None
    routed = routed_agents(config)
    exposed = [reading for reading in readings if reading.paid and reading.agent in routed]
    if exposed:
        names = ", ".join(reading.agent for reading in exposed)
        return CheckOutcome(
            id=LANE_CHECK_ID,
            severity=Severity.WARN,
            message=t(
                "These agents answer through a provider, so every model call pays OpenClaw's own "
                "prompt on that plan: {names}. Lanes: {lanes}.",
                names=names,
                lanes=_summary(readings),
            ),
            action=t(
                "Give the agent that does the work its own harness in the squad (`harness: claude`, "
                "`codex` or `opencode`), or leave it knowingly on the provider lane with "
                "`harness: none`. The three lanes are in docs/squad.md.",
            ),
        )
    return CheckOutcome(
        id=LANE_CHECK_ID,
        severity=Severity.OK,
        message=t("Agent lanes: {lanes}.", lanes=_summary(readings)),
    )


def check_acp_binding(config: dict[str, Any]) -> CheckOutcome | None:
    """Warn when an ACP runtime has no binding: that lane answers as a plain model instead."""
    acp_agents = [reading.agent for reading in lanes(config) if reading.kind == ACP]
    if not acp_agents:
        return None
    bindings = config.get("bindings")
    bound = {
        str(one.get("agentId"))
        for one in (bindings if isinstance(bindings, list) else [])
        if isinstance(one, Mapping) and str(one.get("type") or "").strip().lower() == ACP
    }
    unbound = [agent for agent in acp_agents if agent not in bound]
    if unbound:
        return CheckOutcome(
            id=BINDING_CHECK_ID,
            severity=Severity.WARN,
            message=t(
                "These agents declare an ACP runtime but no ACP binding routes a conversation to "
                "them, so a direct chat answers with the default model instead of the harness: {names}.",
                names=", ".join(unbound),
            ),
            action=t(
                "Declare the harness in the squad and run 'nexgen openclaw setup': it writes the "
                "runtime block and the binding together.",
            ),
        )
    return CheckOutcome(
        id=BINDING_CHECK_ID,
        severity=Severity.OK,
        message=t("Every ACP runtime has its binding: {names}.", names=", ".join(acp_agents)),
    )
