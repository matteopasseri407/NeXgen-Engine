"""The loopback OpenClaw hands Claude Code, judged for the agents that run on `claude-cli`.

One job: read the two files in force and say whether a `claude-cli` agent's connectors
are kept off OpenClaw's `alwaysLoad` loopback server and reachable through Claude Code's
native configuration instead. Silence when the machine has no OpenClaw, no agent on
`claude-cli`, or a configuration this engine cannot read: those are other checks'
answers, not this one's.

A configuration that fails here is the 943k-token incident: every connector's schema in
the prompt of every call, on a subscription whose context is not free. The doctor is the
only place that asks, because a guard cycle judges what a render wrote, and this is about
the machine as it stands now.

The expected connector list comes from the same renderer `nexgen sync` uses
(`McpRenderer.load_resolved_servers("claude")`), so this check cannot demand a server in
Claude Code's file that the manifest does not put there.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.claude_cli import claude_cli_agents
from nexgen_core.openclaw.claude_loopback import pattern_for
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.paths import claude_config, openclaw_config
from nexgen_core.report import CheckOutcome, Severity

#: The id a person reads, in the same family as the rest of the harness checks.
CHECK_ID = "openclaw.claude_loopback"


def _dict(value: Any) -> dict[str, Any]:
    """The value when it is a dict, an empty one otherwise."""
    return value if isinstance(value, dict) else {}


def _servers(config: dict[str, Any]) -> list[str]:
    """The connector names the OpenClaw configuration mounts."""
    return sorted(str(name) for name in _dict(_dict(config.get("mcp")).get("servers")))


def _deny_of(config: dict[str, Any], agent_id: str) -> set[str]:
    """The tool patterns one agent's `deny` list carries."""
    entry = _dict(_dict(_dict(config.get("agents")).get("entries")).get(agent_id))
    deny = _dict(entry.get("tools")).get("deny")
    return {str(item) for item in deny} if isinstance(deny, list) else set()


def _undeclared(config: dict[str, Any], agents: list[str], servers: list[str]) -> list[str]:
    """The connectors some `claude-cli` agent can still reach through the loopback.

    One `agent: server` entry per offender, so the message names the agent a person
    has to look at instead of only the server.
    """
    wanted = [(server, pattern_for(server)) for server in servers]
    missing: list[str] = []
    for agent_id in agents:
        deny = _deny_of(config, agent_id)
        missing.extend(
            f"{agent_id}: {server}" for server, pattern in wanted if pattern and pattern not in deny
        )
    return sorted(missing)


def _native_names(home: Path, vault_data: Path | None, engine_root: Path | None) -> set[str] | None:
    """The connectors Claude Code's own configuration must carry, or None when the manifest cannot be read."""
    try:
        from nexgen_core.renderer import McpRenderer

        renderer = McpRenderer(home=home, vault_data=vault_data, engine_root=engine_root)
        return set(renderer.load_resolved_servers("claude"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable manifest is reported by mcp.manifest; here it only leaves the file's presence to say"
        return None


def _native_connectors_missing(
    home: Path,
    servers: list[str],
    vault_data: Path | None,
    engine_root: Path | None,
) -> list[str]:
    """The connectors the file lacks, among the ones this gateway carries and Claude Code receives.

    A server the manifest keeps off the `claude` target legitimately has no entry there,
    and asking for it would be a false alarm about a router that works.
    """
    config, _error = read_config(claude_config(home))
    if config is None:
        # A file that does not parse is `mcp.rendered_configs`' answer: this check judges
        # where the connectors live, not whether the JSON is well formed.
        return []
    expected = _native_names(home, vault_data, engine_root)
    wanted = {name for name in servers if expected is None or name in expected}
    if not wanted:
        return []
    carried = {str(name) for name in _dict(config.get("mcpServers"))}
    return sorted(wanted - carried)


def check_claude_loopback(
    home: Path,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
) -> CheckOutcome | None:
    """One outcome for the whole question, or None when there is nothing here to judge."""
    path = openclaw_config(home)
    if not path.is_file():
        return None
    config, _error = read_config(path)
    if config is None:
        return None
    agents = claude_cli_agents(config)
    if not agents:
        return None
    servers = _servers(config)
    reachable = _undeclared(config, agents, servers)
    native_path = claude_config(home)
    file_missing = not native_path.is_file()
    native = [] if file_missing else _native_connectors_missing(home, servers, vault_data, engine_root)
    if reachable or native or file_missing:
        problems: list[str] = []
        if reachable:
            problems.append(t("still on the loopback server: {connectors}", connectors=", ".join(reachable)))
        if file_missing:
            problems.append(t("Claude Code's own configuration does not exist ({path})", path=str(native_path)))
        if native:
            problems.append(t("missing from Claude Code's own configuration: {connectors}",
                              connectors=", ".join(native)))
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t("An agent runs on Claude Code's CLI, but its connectors are not where they belong: {problems}.",
                      problems="; ".join(problems)),
            action=t("Run 'nexgen openclaw setup': it denies every connector on the loopback server and renders "
                     "Claude Code's own configuration, where they load lazily."),
            detail=", ".join(agents),
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=t("The {count} agent(s) on Claude Code's CLI keep every connector off the loopback server, and "
                  "Claude Code's own configuration carries them.", count=len(agents)),
    )
