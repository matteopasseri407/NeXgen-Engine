"""The squad's subagent policy: who may spawn whom, on which model, and the ceilings.

nexgen: one-job reason="the squad's subagent policy: the per-agent allowlist and child model, and the four ceilings under agents.defaults"
One job: turn the two `subagents` blocks of squad.yaml into the OpenClaw keys
that decide which agents one agent may spawn, on which model its children run,
and how much fan-out the gateway admits at all.

Why it exists. OpenClaw reads an absent `subagents.allowAgents` as "the same agent
only" (`docs/gateway/config-agents/entries-and-multi-agent.md`, pinned release),
so without this block the head of the squad cannot create a specialist as a
child: it delegates by message and every long task keeps a context of its own.
OpenClaw's own ceilings are looser than this squad wants, too: depth 5, five
children per session, eight concurrent runs and no timeout at all
(`docs/tools/subagents/nesting.md`), which is a fan-out with no floor under it.

Two levels, spelled like the config they write:

- the root `subagents:` block becomes `agents.defaults.subagents`, the ceilings;
- `agents.<id>.subagents: {allow: [...], model: "provider/model"}` becomes
  `agents.entries.<id>.subagents.allowAgents` and `.model`. The model is the
  same-agent fallback: a cross-agent spawn runs on the target's own model
  (`docs/tools/subagents/tool-reference.md`).

Three rules are not preferences:

- the allowlist names agents the squad declares. A wildcard is refused: an
  allowlist that follows the roster is the whole point, and `["*"]` also permits
  an agent the file never mentions;
- the four ceilings are written even when the squad says nothing, because an
  install that declares nothing otherwise inherits OpenClaw's looser defaults;
- `runTimeoutSeconds: 0` is refused with the reason: in OpenClaw it means no
  timeout, so a child that never answers runs until somebody stops it.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

#: The squad key at both levels, and the only keys one agent's block accepts. The
#: value side is the config's spelling, which differs once: `allow` is `allowAgents`.
BLOCK_KEY = "subagents"
_AGENT_KEYS = {"allow": "allowAgents", "model": "model"}

#: The ceilings a squad may override, with the range OpenClaw documents for each
#: (`docs/tools/subagents/nesting.md`). `None` as an upper bound is "no documented
#: ceiling": a positive whole number is then the whole rule.
_LIMITS: dict[str, tuple[int, int | None]] = {
    "maxSpawnDepth": (1, 5),
    "maxChildrenPerAgent": (1, 20),
    "maxConcurrent": (1, None),
    "runTimeoutSeconds": (1, None),
}

#: The ceilings written when the file declares none. Depth 2 leaves the head's
#: children able to spawn leaves and nothing deeper; six children bound one
#: fan-out; four concurrent runs bound what one orchestrator starts at once; 900
#: seconds bound a run that never answers.
DEFAULT_LIMITS: dict[str, int] = {
    "maxSpawnDepth": 2,
    "maxChildrenPerAgent": 6,
    "maxConcurrent": 4,
    "runTimeoutSeconds": 900,
}


def _whole(key: str, value: Any) -> int:
    """One ceiling OpenClaw can honour, or a refusal that names it and the range."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"subagents.{key} must be a whole number, not {value!r}: "
            "OpenClaw reads these four as counts and seconds, never as text"
        )
    if key == "runTimeoutSeconds" and value == 0:
        raise ValueError(
            "subagents.runTimeoutSeconds: 0 means no timeout at all in OpenClaw, so a child that "
            "never answers would run until somebody stops it: give it seconds (900 is the default)"
        )
    low, high = _LIMITS[key]
    if value < low or (high is not None and value > high):
        allowed = f"{low} to {high}" if high is not None else f"{low} or more"
        raise ValueError(f"subagents.{key} is {value}, and OpenClaw accepts {allowed}")
    return value


def limits(squad: Mapping[str, Any] | None) -> dict[str, int]:
    """The ceilings one squad declares, validated, or {} when it declares none."""
    raw = (squad or {}).get(BLOCK_KEY)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"subagents must be a mapping of ceilings ({', '.join(_LIMITS)}), not {raw!r}"
        )
    unknown = sorted(str(key) for key in raw if key not in _LIMITS)
    if unknown:
        raise ValueError(
            f"unknown subagents keys: {', '.join(unknown)}: the ceilings are {', '.join(_LIMITS)}"
        )
    return {key: _whole(key, value) for key, value in raw.items()}


def _allow(agent_id: str, declared: Any, known: Collection[str]) -> list[str]:
    """The ids one agent may spawn, each one the squad declares, in declared order."""
    if not isinstance(declared, list):
        raise ValueError(
            f"agent '{agent_id}': subagents.allow must be a list of agent ids, not {declared!r}"
        )
    if not all(isinstance(item, str) and item.strip() for item in declared):
        raise ValueError(
            f"agent '{agent_id}': every subagents.allow entry must be an agent id, "
            f"and this list carries something else: {declared!r}"
        )
    unknown = [item.strip() for item in declared if item.strip() not in known]
    if unknown:
        raise ValueError(
            f"agent '{agent_id}' may spawn {', '.join(unknown)}, and the squad declares none of "
            f"them: the target must be an agent OpenClaw can route. The squad declares: "
            f"{', '.join(sorted(known))}"
        )
    return list(dict.fromkeys(item.strip() for item in declared))


def _model(agent_id: str, declared: Any) -> str:
    """The `provider/model` reference a child of this agent runs on."""
    ref = declared.strip() if isinstance(declared, str) else ""
    if not ref:
        raise ValueError(
            f"agent '{agent_id}': subagents.model must be a provider/model reference, "
            f"not {declared!r}"
        )
    return ref


def _agent_block(agent_id: str, spec: Mapping[str, Any], known: Collection[str]) -> dict[str, Any]:
    """One agent's block in the config's shape, or {} when the agent declares none."""
    declared = spec.get(BLOCK_KEY)
    if declared is None:
        return {}
    if not isinstance(declared, Mapping):
        raise ValueError(
            f"agent '{agent_id}': subagents must be a mapping with allow and model, not {declared!r}"
        )
    unknown = sorted(str(key) for key in declared if key not in _AGENT_KEYS)
    if unknown:
        raise ValueError(
            f"agent '{agent_id}': unknown subagents keys: {', '.join(unknown)}: "
            "an agent declares allow and model"
        )
    written: dict[str, Any] = {}
    if "allow" in declared:
        written["allowAgents"] = _allow(agent_id, declared["allow"], known)
    if "model" in declared:
        written["model"] = _model(agent_id, declared["model"])
    return written


def _apply_agents(config: dict[str, Any], squad: Mapping[str, Any] | None) -> None:
    """Each agent's own block, merged into the entry that agent already has."""
    agents = (squad or {}).get("agents")
    if not isinstance(agents, Mapping):
        return
    known = {str(agent_id) for agent_id in agents}
    for agent_id, spec in agents.items():
        if not isinstance(spec, Mapping):
            continue
        written = _agent_block(str(agent_id), spec, known)
        if not written:
            continue
        entry = config.setdefault("agents", {}).setdefault("entries", {}).setdefault(str(agent_id), {})
        block = entry.get(BLOCK_KEY) if isinstance(entry.get(BLOCK_KEY), Mapping) else {}
        entry[BLOCK_KEY] = {**block, **written}


def _apply_limits(config: dict[str, Any], squad: Mapping[str, Any] | None) -> None:
    """The ceilings under `agents.defaults`: the squad's value, or the safe one.

    The four are the squad's to own, so a value already in the config loses to the
    declared one and to the default. Every other key in that block is kept: the
    housekeeping `model` `runtime.py` writes lives there, and so may settings the
    engine does not know.
    """
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    existing = defaults.get(BLOCK_KEY) if isinstance(defaults.get(BLOCK_KEY), Mapping) else {}
    defaults[BLOCK_KEY] = {**existing, **DEFAULT_LIMITS, **limits(squad)}


def apply(config: dict[str, Any], squad: Mapping[str, Any] | None) -> None:
    """Writes both levels of the subagent policy into an OpenClaw config, in place."""
    _apply_agents(config, squad)
    _apply_limits(config, squad)
