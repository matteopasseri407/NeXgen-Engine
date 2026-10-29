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
(`McpRenderer.load_resolved_servers("claude")`), so this check cannot demand a server in
Claude Code's file that the manifest does not put there. An `agy` agent is judged, like in
the plan, only on the connectors `agy`'s own file really carries: the ones it does not
carry are reachable through the loopback alone, and no deny may take them away.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.claude_loopback import pattern_for
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.native_connectors import loopback_deny
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
    missing = _native_connectors_missing(home, servers, vault_data, engine_root)
    if missing:
        found.append(t("missing from Claude Code's own configuration: {connectors}",
                       connectors=", ".join(missing)))
    return found


def _agy_problems(config: dict[str, Any], servers: list[str], agents: list[str]) -> list[str]:
    """What is wrong with the agents on `agy`, whose only lost road is the loopback one."""
    reachable = _undeclared(config, agents, servers)
    if not reachable:
        return []
    return [t("still on the loopback server for an agent on 'agy': {connectors}",
              connectors=", ".join(reachable))]


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
        return t("Run 'nexgen openclaw setup': it denies every connector on the loopback server that "
                 "'agy' already carries in its own configuration.")
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
    servers = _servers(config)
    deny = loopback_deny(config, servers, home)
    if not deny.claude and not deny.agy:
        return None
    problems: list[str] = []
    if deny.claude:
        problems.extend(_claude_problems(config, home, servers, deny.claude, vault_data, engine_root))
    if deny.agy:
        problems.extend(_agy_problems(config, deny.agy_connectors, deny.agy))
    detail = ", ".join([*deny.claude, *deny.agy])
    if problems:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=_message(bool(deny.claude), bool(deny.agy), problems),
            action=_action(bool(deny.agy)),
            detail=detail,
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=_ok(len(deny.claude) + len(deny.agy), bool(deny.agy)),
        detail=detail,
    )
