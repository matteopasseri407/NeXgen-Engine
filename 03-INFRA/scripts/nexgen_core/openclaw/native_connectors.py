"""Which agents read their connectors from a file their own CLI owns.

One job: answer, from a configuration dict and the home it belongs to, which agents must
have OpenClaw's connectors denied on its loopback server because their own CLI already
carries them -- Claude Code's `~/.claude.json` for an agent on `claude-cli`, and `agy`'s MCP
configuration for an agent on `google-antigravity-cli`.

Why one answer and not two: the same list decides what a setup writes in `tools.deny` and
what the doctor reports as a fault, and two readings would drift apart on the first agent
added by hand. The `claude-cli` half is not answered here: `claude_cli.claude_cli_agents`
owns it, and this module asks it. The `agy` half asks the same `claude_cli.runtime_id`, in
OpenClaw's own order, and adds one rule of the backend: a CLI backend plugin's provider id
and its runtime id are the same string, so `google-antigravity-cli/gemini-3.8-flash` runs
`agy` even when no `agentRuntime` is pinned anywhere in the file -- the shape `terms` reads
as well.

The second half of the question is the machine, not the file. `agy` receives the connectors
directly from the engine's renderer, so an `agy` agent is named only when that file exists
here: on a machine where the engine never wrote it, the loopback copy is the only copy the
agent has, and denying it would leave the agent with no connector at all.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.openclaw.claude_cli import (
    CLAUDE_CLI,
    agent_ref,
    claude_cli_agents,
    runtime_id,
    split_ref,
)
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND
from nexgen_core.paths import antigravity_configs

#: The runtime id OpenClaw uses for an agent driven by the local `agy` CLI. The provider of
#: a standalone route names it too (`google-antigravity-cli/<model>`), because a CLI backend
#: plugin is addressed by that id in both places.
ANTIGRAVITY_CLI = LOCAL_AGY_BACKEND

#: The runtimes whose own configuration carries the connectors, and where a deny on the
#: loopback server is the right answer.
NATIVE_RUNTIMES = (CLAUDE_CLI, ANTIGRAVITY_CLI)


def _entries(config: dict[str, Any]) -> dict[str, Any]:
    """`agents.entries`, or an empty map: a configuration is data, never a shape to trust."""
    agents = config.get("agents")
    entries = agents.get("entries") if isinstance(agents, dict) else None
    return entries if isinstance(entries, dict) else {}


def runtime_of(config: dict[str, Any], agent_id: str) -> str | None:
    """The CLI runtime this agent runs on, when it is one of the two this module knows.

    The pinned answer comes first, and from one place (`claude_cli.runtime_id`). The
    provider of the ref is the second only because OpenClaw selects a CLI backend either
    from `agentRuntime.id` or from the provider prefix of a standalone route, and for such
    a plugin the two are the same string.
    """
    ref = agent_ref(config, agent_id)
    if not ref:
        return None
    pinned = runtime_id(config, agent_id, ref)
    if pinned:
        return pinned
    provider, _model = split_ref(ref)
    return provider if provider in NATIVE_RUNTIMES else None


def agy_agents(config: dict[str, Any]) -> list[str]:
    """Every agent in this configuration that runs on the local `agy` CLI, in the file's order."""
    return [str(agent_id) for agent_id in _entries(config)
            if runtime_of(config, str(agent_id)) == ANTIGRAVITY_CLI]


def agy_config(home: Path | None = None) -> Path | None:
    """The file `agy` reads its connectors from on this machine, or None when there is none.

    The canonical file is asked first; a machine Antigravity itself configured may only
    carry one of the fan-out copies, and every copy holds the same content, so the first
    file that exists wins.
    """
    return next((path for path in antigravity_configs(home) if path.is_file()), None)


def denied_agents(config: dict[str, Any], home: Path | None = None) -> list[str]:
    """Every agent whose connectors must be denied on the loopback server here, in file order.

    An `agy` agent is named only when `agy`'s own configuration exists on this machine:
    without it the agent has no other copy of those connectors, and the deny would take
    away a tool rather than move it.
    """
    agents = claude_cli_agents(config)
    agy = agy_agents(config) if agy_config(home) is not None else []
    seen = set(agents)
    return [*agents, *(agent for agent in agy if agent not in seen)]
