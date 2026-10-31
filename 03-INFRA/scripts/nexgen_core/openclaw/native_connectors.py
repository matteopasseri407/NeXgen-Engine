"""Which agents read their connectors from a file their own CLI owns.

One job: answer, from a configuration dict, the connectors this gateway mounts and a home,
who must have those connectors denied on OpenClaw's loopback server because another road
already carries them -- the engine's `lazy-mcp` gateway for an agent on `claude-cli`, and
`agy`'s MCP configuration for an agent on `google-antigravity-cli` -- and which connectors
the `agy` side may really give up.

Why one answer and not two: the same answer decides what a setup writes in `tools.deny` and
what the doctor reports as a fault, and two readings would drift apart on the first agent
added by hand. The `claude-cli` half is not decided here: `claude_cli.claude_cli_agents`
owns it and this module asks it. The `agy` half asks the same `claude_cli.runtime_id`, in
OpenClaw's own order, and adds one rule of the backend: a CLI backend plugin's provider id
and its runtime id are the same string, so `google-antigravity-cli/gemini-3.8-flash` runs
`agy` even when no `agentRuntime` is pinned anywhere in the file -- the shape `terms` reads
as well.

The second half of the `agy` question is the machine, connector by connector. `agy` receives
the connectors directly from the engine's renderer, and only those may be denied on the
loopback: a connector `agy`'s file does not carry is one the loopback is the only road to,
and denying it would leave the agent with nothing. A file that exists proves nothing either
-- the `google-antigravity-cli` plugin writes its own entries into it for the length of a
turn and leaves it empty afterwards -- so what counts is the server names it holds now, read
tolerantly: no file, an unreadable one and one that is not JSON all mean "carries nothing".
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

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

#: The prefix the `google-antigravity-cli` plugin gives the entries it writes into `agy`'s
#: file for the length of a turn. They are the loopback's own tools, never a connector this
#: gateway carries, and they are gone at the end of the turn.
PLUGIN_PREFIX = "openclaw__"


class LoopbackDeny(NamedTuple):
    """Who keeps the loopback connectors off, and which connectors each side gives up."""

    #: The agents on Claude Code's CLI: every connector is denied for them.
    claude: list[str]
    #: The agents on `agy`: only `agy_connectors` is denied for them.
    agy: list[str]
    #: The connectors `agy` carries itself here, among the ones this gateway mounts.
    agy_connectors: list[str]


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


def agy_servers(home: Path | None = None) -> set[str]:
    """The server names `agy`'s own file holds here, without the plugin's own entries.

    Tolerant by design: a file that cannot be read, parses to something else, or carries no
    `mcpServers` map says the same thing as no file at all -- `agy` has none of the engine's
    connectors here, so a deny would take away the loopback's copy and give nothing back.
    """
    path = agy_config(home)
    if path is None:
        return set()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable agy file is read as carrying nothing: the deny must never rest on a file this code cannot see"
        return set()
    servers = document.get("mcpServers") if isinstance(document, dict) else None
    if not isinstance(servers, dict):
        return set()
    return {str(name) for name in servers if not str(name).startswith(PLUGIN_PREFIX)}


def loopback_deny(
    config: dict[str, Any],
    connectors: Iterable[str],
    home: Path | None = None,
) -> LoopbackDeny:
    """Who is denied the loopback connectors here, and which connectors `agy` may give up.

    An `agy` agent is named only when `agy`'s own file carries at least one of these
    connectors; only those it carries are denied for that agent, and the ones it does not
    carry stay reachable through the loopback, which is their only road to it.
    """
    mounted = [str(name) for name in connectors]
    carried = agy_servers(home)
    direct = [name for name in mounted if name in carried]
    return LoopbackDeny(
        claude=list(claude_cli_agents(config)),
        agy=agy_agents(config) if direct else [],
        agy_connectors=direct,
    )
