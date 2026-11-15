"""Which lane each OpenClaw agent runs on, and the two blocks that make an ACP lane real.

One job: read the `harness:` an agent declares in its squad, say which lane that
puts it on, and write the runtime block *and* the persistent binding that together
make the ACP lane work. Both halves or neither: an ACP runtime with no bound
session answers a direct chat with the default model, which is the bug of
2026-10-23 that this module exists to stop repeating.

Why one owner. The lane used to be decided in three places that could disagree: a
model ref, a per-model `agentRuntime` pin, and a whole-agent `runtime` key the
pinned release ignores. Here it is one function, pure apart from the config it is
handed, so the plan, the setup and (next) the doctor read the same answer.

Why nothing moves on its own. A lane is entered only because the squad says
`harness:` on that agent. An agent without the key keeps exactly the lane it has
today, including the three that call the Go endpoint over HTTP, and the plan of
the proposal's `sviluppo` default is deliberately not applied: that would move an
existing agent nobody asked to move. A squad that wants the native lane writes
`harness: opencode` on `sviluppo` itself.

The three lanes, each with its own cost, are named in `docs/squad.md`.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.acp import (
    ACP_HARNESSES,
    CLI_ONLY_HARNESS,
    HARNESS_TO_ACP,
    acp_block,
    acpx_entry,
    binding,
)
from nexgen_core.openclaw.runtime import acp_runtime

#: The three lanes an agent can be on.
ACP = "acp"
CLI = "cli"
PROVIDER = "provider"

#: The squad key that opts an agent into a harness, and the value meaning "as today".
HARNESS_KEY = "harness"
NO_HARNESS = ""

#: The harnesses that run as a CLI backend instead of an ACP session.
CLI_HARNESSES = (CLI_ONLY_HARNESS,)

#: Why an ACP lane the squad asked for was not written.
UNKNOWN = "unknown harness"
MISSING = "harness not installed"
UNROUTED = "no binding routes to this agent"
NO_WORKSPACE = "no workspace to run in"

#: What a person reads for each of those, written here so the plan and the doctor
#: say the same thing. `t()` is called when the message is built, never at import.
_PROBLEMS: dict[str, str] = {
    UNKNOWN: "Agent {agent}: `harness: {harness}` is not claude, codex, opencode, agy or none, "
             "so the setup wrote nothing for it.",
    MISSING: "Agent {agent}: harness {harness} is not installed on this machine, "
             "so the setup wrote nothing for it.",
    UNROUTED: "Agent {agent}: no channel binding routes a conversation to it, so the setup wrote no "
              "harness lane; add a `bindings` entry naming that agent in squad.yaml.",
    NO_WORKSPACE: "Agent {agent}: it has no workspace folder for the harness to run in, "
                  "so the setup wrote nothing for it.",
}


@dataclass(frozen=True)
class Lane:
    """One agent's lane: what runs it, in which folder, and why it was not applied."""

    agent: str
    kind: str
    harness: str = ""
    model: str = ""
    cwd: str = ""
    #: Empty when the lane is in force (or none was asked for); the reason otherwise.
    reason: str = ""


def _specs(squad: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    """The agents a squad declares, each spec as written."""
    agents = (squad or {}).get("agents")
    if not isinstance(agents, Mapping):
        return {}
    return {str(agent_id): spec for agent_id, spec in agents.items() if isinstance(spec, Mapping)}


def declared_harness(spec: Mapping[str, Any]) -> str:
    """The `harness:` an agent declares, lowercased; "" when it declares none.

    `none` is the explicit way of saying "as today": it is read as no harness at
    all, so a squad can name the provider lane instead of leaving a hole.
    """
    value = spec.get(HARNESS_KEY)
    if not isinstance(value, str):
        return NO_HARNESS
    name = value.strip().lower()
    return NO_HARNESS if name == "none" else name


def model_of(spec: Mapping[str, Any]) -> str:
    """The model ref an agent's spec declares, from either shape the file stores."""
    value = spec.get("model")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        primary = value.get("primary")
        return primary.strip() if isinstance(primary, str) else ""
    return ""


def available(detected: Any, harness: str) -> bool:
    """Whether this machine has the login a declared harness would run on."""
    if harness == "claude":
        return bool(getattr(getattr(detected, "claude", None), "available", False))
    if harness == "codex":
        return bool(getattr(getattr(detected, "codex", None), "available", False))
    if harness == "opencode":
        # The ACP session runs on the CLI's own login: the flat Go key is what the
        # *provider* lane needs, and it is not asked for here.
        return bool(getattr(detected, "opencode", False))
    return False


def lane_of(agent_id: str, spec: Mapping[str, Any], detected: Any, cwd: str = "") -> Lane:
    """The lane one agent is on, with the reason when the lane cannot be entered."""
    declared = declared_harness(spec)
    model = model_of(spec)
    if not declared:
        return Lane(str(agent_id), PROVIDER, model=model, cwd=cwd)
    if declared in HARNESS_TO_ACP:
        if not available(detected, declared):
            return Lane(str(agent_id), ACP, declared, model, cwd, MISSING)
        return Lane(str(agent_id), ACP, declared, model, cwd, "" if cwd else NO_WORKSPACE)
    if declared in CLI_HARNESSES:
        return Lane(str(agent_id), CLI, declared, model, cwd)
    return Lane(str(agent_id), PROVIDER, declared, model, cwd, UNKNOWN)


def lanes(
    squad: Mapping[str, Any] | None, detected: Any, workspaces: Mapping[str, str] | None = None,
) -> tuple[Lane, ...]:
    """Every declared agent's lane, in the order the squad lists them."""
    cwds = workspaces or {}
    return tuple(
        lane_of(agent_id, spec, detected, str(cwds.get(agent_id) or ""))
        for agent_id, spec in _specs(squad).items()
    )


def _entries(config: Mapping[str, Any]) -> dict[str, Any]:
    """The `agents.entries` of a proposal, as written in place."""
    agents = config.get("agents")
    entries = agents.get("entries") if isinstance(agents, Mapping) else None
    return entries if isinstance(entries, dict) else {}


def _workspaces(config: Mapping[str, Any]) -> dict[str, str]:
    """Each proposed agent's workspace, for the harness's `cwd`."""
    return {
        str(agent_id): str(entry.get("workspace"))
        for agent_id, entry in _entries(config).items()
        if isinstance(entry, Mapping) and isinstance(entry.get("workspace"), str) and entry["workspace"].strip()
    }


def _routed_agents(bindings: Any) -> set[str]:
    """The agents some channel binding already routes a conversation to."""
    if not isinstance(bindings, list):
        return set()
    return {str(one.get("agentId")) for one in bindings if isinstance(one, Mapping)}


def _as_acp(bindings: Any, written: Mapping[str, Lane]) -> list[Any]:
    """The same bindings, with every conversation of a harness agent bound to the harness."""
    if not isinstance(bindings, list):
        return []
    rewritten: list[Any] = []
    for one in bindings:
        lane = written.get(str(one.get("agentId"))) if isinstance(one, Mapping) else None
        if lane is None:
            rewritten.append(one)
            continue
        match = one.get("match") if isinstance(one.get("match"), Mapping) else {}
        rewritten.append(binding(lane.agent, lane.harness, lane.cwd, match))
    return rewritten


def _with_bridge(config: dict[str, Any], detected: Any, written: tuple[Lane, ...]) -> None:
    """The acpx plugin and the ACP block, both allowing the harnesses just declared."""
    plugins = dict(config.get("plugins") or {})
    entries = dict(plugins.get("entries") or {})
    allow = [str(item) for item in plugins.get("allow") or []]
    if "acpx" not in allow:
        allow.append("acpx")
    entries["acpx"] = acpx_entry(detected)
    config["plugins"] = {**plugins, "allow": allow, "entries": entries}

    block = dict(config.get("acp") or acp_block(detected, acpx=True) or {})
    allowed = [str(item) for item in block.get("allowedAgents") or []]
    for lane in written:
        if lane.harness not in allowed:
            allowed.append(lane.harness)
    # One order, so two runs of the same squad propose the same file.
    block["allowedAgents"] = [harness for harness in ACP_HARNESSES if harness in allowed]
    config["acp"] = block


def apply(
    config: dict[str, Any], squad: Mapping[str, Any] | None, detected: Any,
) -> tuple[Lane, ...]:
    """Writes both halves of every ACP lane the squad declares, and names the rest.

    The returned lanes are the ones a person has to know about: an unknown value,
    a harness this machine cannot run, an agent no channel binding reaches. They
    carry the reason, and the plan prints them. Nothing is written for a lane with
    a reason, so a half lane cannot be produced here.
    """
    workspaces = _workspaces(config)
    asked = lanes(squad, detected, workspaces)
    entries = _entries(config)
    routed = _routed_agents(config.get("bindings"))
    written: dict[str, Lane] = {}
    reported: list[Lane] = []
    for lane in asked:
        if lane.kind != ACP or lane.reason:
            if lane.reason:
                reported.append(lane)
            continue
        if lane.agent not in routed:
            # A runtime block with no bound session is the 2026-10-23 bug: the person
            # asks the harness and a plain model answers. Neither half is written.
            reported.append(replace(lane, reason=UNROUTED))
            continue
        written[lane.agent] = lane
    if not written:
        return tuple(reported)
    for lane in written.values():
        entries[lane.agent]["runtime"] = acp_runtime(lane.harness, lane.cwd)
    config["bindings"] = _as_acp(config.get("bindings"), written)
    _with_bridge(config, detected, tuple(written.values()))
    return tuple(reported)


def problem(lane: Lane) -> str:
    """What a person reads when the lane they asked for was not written."""
    template = _PROBLEMS.get(lane.reason, "")
    return t(template, agent=lane.agent, harness=lane.harness) if template else ""
