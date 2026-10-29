"""The loopback OpenClaw hands a CLI that carries its own connectors, judged per agent.

One job: read the files in force and say whether the connectors of every agent that reads
them from its own CLI stay off OpenClaw's `alwaysLoad` loopback server and are reachable
through that CLI's own configuration instead -- Claude Code's `~/.claude.json` for an agent
on `claude-cli`, `agy`'s MCP configuration for one on `google-antigravity-cli`. Silence
when the machine has no OpenClaw, no such agent, or a configuration this engine cannot
read: those are other checks' answers, not this one's.

A configuration that fails here is the 943k-token incident: every connector's schema in
the prompt of every call, on a subscription whose context is not free. The doctor is the
only place that asks, because a guard cycle judges what a render wrote, and this is about
the machine as it stands now.

The expected connector list comes from the same renderer `nexgen sync` uses
(`McpRenderer.load_resolved_servers(<target>)`), so this check cannot demand a server in a
CLI's file that the manifest does not put there. An `agy` agent is judged only where `agy`'s
own configuration exists: on a machine that has none, the loopback is the right place for
its connectors, because the engine never wrote them anywhere else.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.claude_cli import claude_cli_agents
from nexgen_core.openclaw.claude_loopback import pattern_for
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.native_connectors import agy_agents, agy_config
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
    """The connectors these agents can still reach through the loopback.

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


def _native_names(home: Path, vault_data: Path | None, engine_root: Path | None,
                  target: str) -> set[str] | None:
    """The connectors one CLI's own configuration must carry, or None when the manifest cannot be read."""
    try:
        from nexgen_core.renderer import McpRenderer

        renderer = McpRenderer(home=home, vault_data=vault_data, engine_root=engine_root)
        return set(renderer.load_resolved_servers(target))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable manifest is reported by mcp.manifest; here it only leaves the file's presence to say"
        return None


def _native_connectors_missing(
    path: Path,
    home: Path,
    servers: list[str],
    target: str,
    vault_data: Path | None,
    engine_root: Path | None,
) -> list[str]:
    """The connectors the file lacks, among the ones this gateway carries and that CLI receives.

    A server the manifest keeps off the CLI's target legitimately has no entry there,
    and asking for it would be a false alarm about a router that works.
    """
    config, _error = read_config(path)
    if config is None:
        # A file that does not parse is `mcp.rendered_configs`' answer: this check judges
        # where the connectors live, not whether the JSON is well formed.
        return []
    expected = _native_names(home, vault_data, engine_root, target)
    wanted = {name for name in servers if expected is None or name in expected}
    if not wanted:
        return []
    carried = {str(name) for name in _dict(config.get("mcpServers"))}
    return sorted(wanted - carried)


def _claude_problems(
    config: dict[str, Any],
    home: Path,
    servers: list[str],
    agents: list[str],
    vault_data: Path | None,
    engine_root: Path | None,
) -> list[str]:
    """What is wrong with the agents on Claude Code's CLI, in the words a person reads."""
    found: list[str] = []
    reachable = _undeclared(config, agents, servers)
    if reachable:
        found.append(t("still on the loopback server: {connectors}", connectors=", ".join(reachable)))
    path = claude_config(home)
    if not path.is_file():
        found.append(t("Claude Code's own configuration does not exist ({path})", path=str(path)))
        return found
    missing = _native_connectors_missing(path, home, servers, "claude", vault_data, engine_root)
    if missing:
        found.append(t("missing from Claude Code's own configuration: {connectors}",
                       connectors=", ".join(missing)))
    return found


def _agy_problems(
    config: dict[str, Any],
    home: Path,
    path: Path,
    servers: list[str],
    agents: list[str],
    vault_data: Path | None,
    engine_root: Path | None,
) -> list[str]:
    """What is wrong with the agents on `agy`, whose connectors live in its own file."""
    found: list[str] = []
    reachable = _undeclared(config, agents, servers)
    if reachable:
        found.append(t("still on the loopback server for an agent on 'agy': {connectors}",
                       connectors=", ".join(reachable)))
    missing = _native_connectors_missing(path, home, servers, "antigravity", vault_data, engine_root)
    if missing:
        found.append(t("missing from the configuration 'agy' reads ({path}): {connectors}",
                       path=str(path), connectors=", ".join(missing)))
    return found


def _message(has_claude: bool, has_agy: bool, problems: list[str]) -> str:
    """The one line a person reads, in the words of the CLIs really involved here."""
    joined = "; ".join(problems)
    if has_claude and has_agy:
        return t("Agents run on CLIs that carry their own connectors, but they are not where they "
                 "belong: {problems}.", problems=joined)
    if has_agy:
        return t("An agent runs on 'agy', but its connectors are not where they belong: {problems}.",
                 problems=joined)
    return t("An agent runs on Claude Code's CLI, but its connectors are not where they belong: "
             "{problems}.", problems=joined)


def _action(has_agy: bool) -> str:
    """The one thing to run, named for the CLI whose files are the ones out of step."""
    if has_agy:
        return t("Run 'nexgen openclaw setup': it denies every connector on the loopback server, "
                 "and 'agy' reads them from its own configuration, which 'nexgen sync' writes.")
    return t("Run 'nexgen openclaw setup': it denies every connector on the loopback server and renders "
             "Claude Code's own configuration, where they load lazily.")


def _ok(count: int, has_agy: bool) -> str:
    """The line said when every agent judged keeps its connectors off the loopback."""
    if has_agy:
        return t("The {count} agent(s) that read their connectors from their own CLI keep every connector "
                 "off the loopback server, and that CLI's configuration carries them.", count=count)
    return t("The {count} agent(s) on Claude Code's CLI keep every connector off the loopback server, and "
             "Claude Code's own configuration carries them.", count=count)


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
    claude = claude_cli_agents(config)
    agy_path = agy_config(home)
    agy = agy_agents(config) if agy_path is not None else []
    if not claude and not agy:
        return None
    servers = _servers(config)
    problems: list[str] = []
    if claude:
        problems.extend(_claude_problems(config, home, servers, claude, vault_data, engine_root))
    if agy and agy_path is not None:
        problems.extend(_agy_problems(config, home, agy_path, servers, agy, vault_data, engine_root))
    detail = ", ".join([*claude, *agy])
    if problems:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=_message(bool(claude), bool(agy), problems),
            action=_action(bool(agy)),
            detail=detail,
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=_ok(len(claude) + len(agy), bool(agy)),
        detail=detail,
    )
