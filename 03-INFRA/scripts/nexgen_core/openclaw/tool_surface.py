"""The lean tool surface one agent may opt into, written as `tools.byProvider`.

One job: turn `tool_surface: lean` on an agent into the `tools.byProvider` entry
for that agent's model, listing every tool the agent's declared capabilities need
and nothing else. Why it exists, with the measurement: on the `claude-cli` road
the gateway's own catalogue reaches Claude Code eagerly (`alwaysLoad: true`), so
51 tool schemas ride in every model call and are 112,239 of the ~264,000 prompt
characters, about 33,000 of the 78,000 tokens a call costs. A `profile: minimal`
plus an `alsoAllow` list is the documented way to publish fewer of them, and it is
the L1 lever of `docs/proposals/daru-burn.md`.

Three rules are not preferences:

- nothing is written unless the agent says `tool_surface: lean`, so an existing
  agent keeps the surface it runs today;
- the list is derived, never hand-kept: the supervisor set comes from
  `tools_profile`, which also decides which agent supervises, and the connector
  road comes from the `lazy-mcp` meta-tools, the one way a denied connector stays
  reachable;
- `allow` is never written beside the agent's own `alsoAllow` (OpenClaw refuses
  both in one scope). This lives in `tools.byProvider`, a different scope, and
  uses `alsoAllow` only.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nexgen_core.mcp_check import META_TOOLS
from nexgen_core.openclaw.claude_cli import split_ref
from nexgen_core.openclaw.tools_profile import SUPERVISOR_TOOLS, supervisor_agent

#: The squad key that opts one agent in, and the one value it accepts today.
SURFACE_KEY = "tool_surface"
LEAN = "lean"

#: The profile OpenClaw publishes for a lean surface: its own minimal allowlist
#: (`session_status` and the gateway maintenance tools), which `alsoAllow` extends.
PROFILE = "minimal"

#: What a CLI-backend agent keeps. Measured on the capo turn this list comes from
#: (`docs/proposals/daru-burn.md`, L1): shell, files, web, the session tools, memory,
#: and the two ways to reach the person. Everything else in the 51 eager schemas —
#: browser, canvas, plugins, nodes, tts, the goal tools — is not on that road.
LEAN_TOOLS = (
    "apply_patch", "ask_user", "edit", "exec", "memory_get", "memory_search",
    "message", "process", "read", "sessions_history", "sessions_list",
    "sessions_send", "sessions_yield", "session_status", "web_fetch", "web_search",
    "write",
)

#: The `lazy-mcp` server as OpenClaw names it: `server__tool`. Connectors are denied
#: on the loopback by `loopback_provider_policy`, so these four are the whole road
#: back to the Vault, GitHub, Vercel and the rest, and they are never dropped.
LAZY_SERVER = "lazy-mcp"


def declared_surface(spec: Mapping[str, Any]) -> str:
    """The `tool_surface` one agent declares, lowercased, or "" when it declares none."""
    value = spec.get(SURFACE_KEY)
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def lazy_tools() -> tuple[str, ...]:
    """The connector road as OpenClaw names it, from the gateway's own meta-tools."""
    return tuple(f"{LAZY_SERVER}__{name}" for name in sorted(META_TOOLS))


def lean_tools(agent_id: str, supervisor: str) -> list[str]:
    """Every tool a lean agent keeps, sorted and deduplicated.

    The supervisor set is added for the agent that supervises and only for it: the
    plugin registers those tools `optional`, so a surface without them takes the
    supervision away from the one agent that drives it.
    """
    tools = [*LEAN_TOOLS, *lazy_tools()]
    if str(agent_id) == supervisor:
        tools.extend(SUPERVISOR_TOOLS)
    return sorted(dict.fromkeys(tools))


def entry(agent_id: str, supervisor: str) -> dict[str, Any]:
    """The `tools.byProvider` value for one lean agent."""
    return {"profile": PROFILE, "alsoAllow": lean_tools(agent_id, supervisor)}


def surface_entries(
    squad: Mapping[str, Any] | None, supervisor: str,
) -> dict[str, dict[str, Any]]:
    """The byProvider entries the squad asks for, keyed by the model each agent runs.

    The key is the model, not the agent, because that is what `tools.byProvider`
    means: two agents sharing a model share the surface, which `docs/squad.md` says
    in as many words. An agent without a model of its own is skipped — inheriting
    one would write a surface for a model the agent never named.
    """
    agents = (squad or {}).get("agents")
    if not isinstance(agents, Mapping):
        return {}
    wanted: dict[str, dict[str, Any]] = {}
    for agent_id, spec in agents.items():
        if not isinstance(spec, Mapping) or declared_surface(spec) != LEAN:
            continue
        model = spec.get("model")
        if isinstance(model, Mapping):
            model = model.get("primary")
        ref = model.strip() if isinstance(model, str) else ""
        provider, name = split_ref(ref)
        if not provider or not name:
            continue
        wanted[ref] = entry(str(agent_id), supervisor)
    return wanted


def apply(config: dict[str, Any], squad: Mapping[str, Any] | None) -> None:
    """Writes the lean entries into `tools.byProvider`, merging what is already there.

    A model the engine cages (a local model with `profile: messaging` and its deny)
    keeps that cage: the two profiles cannot both be in force, and the cage is the
    one that stops a local model from writing. An `alsoAllow` already on the entry
    is unioned rather than replaced, so a person's own additions survive a setup.
    """
    wanted = surface_entries(squad, supervisor_agent(config))
    if not wanted:
        return
    tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    by_provider = dict(tools.get("byProvider") or {})
    for ref, wanted_entry in wanted.items():
        existing = by_provider.get(ref)
        if isinstance(existing, Mapping) and existing.get("profile") not in (None, PROFILE):
            continue
        merged = dict(existing) if isinstance(existing, Mapping) else {}
        merged.update(wanted_entry)
        current_allow = existing.get("alsoAllow") if isinstance(existing, Mapping) else None
        if isinstance(current_allow, list):
            merged["alsoAllow"] = sorted(
                dict.fromkeys([*[str(item) for item in current_allow], *wanted_entry["alsoAllow"]])
            )
        by_provider[ref] = merged
    config["tools"] = {**tools, "byProvider": by_provider}
