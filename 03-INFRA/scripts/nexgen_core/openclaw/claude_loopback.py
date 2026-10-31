"""The connectors an agent must not see through OpenClaw's loopback server.

One job: deny the `<server>__*` patterns the caller names, on the agents the caller names --
the connectors the OpenClaw configuration mounts, kept off the loopback MCP server OpenClaw
hands a CLI with `alwaysLoad: true`. What stays is the agent's own OpenClaw toolset --
messages, sessions, the supervisor's `nexgen_sup_*` -- and what disappears is the one thing
that made a single call cost 943k tokens: every connector schema in the prompt at every
request. Two kinds of agent need it, for the same reason: one on `claude-cli`, whose
connectors live in Claude Code's own configuration, and one on `agy`, whose connectors live
in `agy`'s. `native_connectors` says who they are and which connectors each may give up.

The connectors are not lost. They reach the agent through the engine's `lazy-mcp`
gateway, which OpenClaw mounts in `mcp.servers` beside them (a normal entry, so
OpenClaw's own Tool Search still defers it): OpenClaw starts Claude Code with
`--strict-mcp-config`, so Claude Code's own `~/.claude.json` is not what the agent
reads. `claude_native` keeps that file aligned for the sessions that read it
directly; `agy` reads the file `mcp_render.antigravity` writes.

Two rules are not preferences:

- the patterns are added to whatever `deny` the agent already has, never instead of
  it, and never touching `allow`/`alsoAllow`: OpenClaw refuses `allow` beside
  `alsoAllow` in one scope, and an agent's own permission list is the person's;
- an agent that does not run on `claude-cli` is left exactly as it was, so a machine
  that never had this problem cannot lose a tool to a change about it.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from nexgen_core.openclaw.claude_cli import claude_cli_agents

#: How OpenClaw spells a connector's tools: `<server>__<tool>`, so the wildcard
#: covers a tool the server grows tomorrow without anyone editing this file.
SUFFIX = "__*"

#: Names that are not connectors of the configuration: OpenClaw's own loopback
#: server lives in the same namespace, and denying it would deny the agent's own
#: toolset -- the whole point of the deny is to keep exactly that. `lazy-mcp` is the
#: gateway that serves the connectors once they are off the loopback: denying it
#: would leave the agent with no road at all.
NOT_A_CONNECTOR = frozenset({"openclaw", "lazy-mcp"})


def pattern_for(server: str) -> str | None:
    """The deny pattern that covers one connector, or None when the name is not one."""
    name = str(server or "").strip()
    if not name or name in NOT_A_CONNECTOR:
        return None
    return f"{name}{SUFFIX}"


def patterns(servers: Iterable[str]) -> list[str]:
    """One `<server>__*` pattern per connector, sorted and without duplicates."""
    return sorted({pattern for server in servers if (pattern := pattern_for(server))})


def _deny_for(entry: dict[str, Any], added: list[str]) -> list[str]:
    """The agent's deny list with the patterns added, sorted, keeping what was there."""
    tools = entry.get("tools") if isinstance(entry.get("tools"), dict) else {}
    existing = tools.get("deny") if isinstance(tools.get("deny"), list) else []
    merged = {str(item) for item in [*existing, *added]}
    return sorted(merged)


def apply(
    config: dict[str, Any],
    servers: Iterable[str],
    agents: Iterable[str] | None = None,
) -> dict[str, Any]:
    """The same configuration with every connector denied on its `claude-cli` agents.

    Returns a new document: the caller's proposal is data it may still print, and a
    plan that mutated it would describe a machine that no longer matches the one
    written. Nothing is added when there is no pattern, so an engine on a gateway
    with no connector writes nothing at all.

    `agents` carries that answer when the caller already has it, from the document
    the write will leave rather than from `config` alone; None means the answer is
    read from `config` here. An id `config` does not declare yet receives the
    smallest entry that carries the deny -- the merge puts it inside the entry the
    file already has -- because an agent the proposal dropped is still an agent the
    file runs.
    """
    added = patterns(servers)
    agents_to_fix = set(claude_cli_agents(config)) if agents is None else {str(agent) for agent in agents}
    if not added or not agents_to_fix:
        return config
    agents_block = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    entries = agents_block.get("entries") if isinstance(agents_block.get("entries"), dict) else {}
    updated: dict[str, Any] = {}
    for agent_id in [*entries, *sorted(agents_to_fix - set(entries))]:
        if agent_id not in agents_to_fix:
            continue
        entry = entries.get(agent_id, {})
        if not isinstance(entry, dict):
            continue
        tools = dict(entry.get("tools")) if isinstance(entry.get("tools"), dict) else {}
        tools["deny"] = _deny_for(entry, added)
        updated[agent_id] = {**entry, "tools": tools}
    if not updated:
        return config
    return {
        **config,
        "agents": {**agents_block, "entries": {**entries, **updated}},
    }
