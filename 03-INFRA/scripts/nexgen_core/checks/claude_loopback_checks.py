"""The loopback OpenClaw hands a CLI that carries its own connectors, judged per agent.

One job: read the files in force and say whether the connectors of every agent that
reads them from its own CLI stay off OpenClaw's `alwaysLoad` loopback server and are
reachable through the road that remains -- the engine's `lazy-mcp` gateway under
`mcp.servers` for an agent on `claude-cli`, `agy`'s own MCP configuration for one on
`google-antigravity-cli`. Silence when the machine has no OpenClaw, no such agent, or
a configuration this engine cannot read: those are other checks' answers, not this
one's.

A configuration that fails here is the 943k-token incident: every connector's schema in
the prompt of every call, on a subscription whose context is not free. The doctor is the
only place that asks, because a guard cycle judges what a render wrote, and this is about
the machine as it stands now.

An `agy` agent is judged, like in the plan, only on the connectors `agy`'s own file
really carries: the ones it does not carry are reachable through the loopback alone, and
no deny may take them away.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.mcp_placement import GATEWAY
from nexgen_core.mcp_render.openclaw import is_engine_lazy_mcp
from nexgen_core.openclaw.claude_cli import CLAUDE_CLI
from nexgen_core.openclaw.claude_loopback import pattern_for
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.loopback_provider_policy import routed_keys, undeclared_provider_patterns
from nexgen_core.openclaw.native_connectors import ANTIGRAVITY_CLI, loopback_deny
from nexgen_core.openclaw.node_only import detect_node_only, node_only_outcome
from nexgen_core.paths import openclaw_config
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


def _gateway_missing(config: dict[str, Any]) -> bool:
    """Whether the engine's gateway is absent from OpenClaw's `mcp.servers`.

    It is the only road a `claude-cli` agent's connectors have left once the deny keeps
    them off the loopback, so a missing (or hand-written) entry is the whole fault.
    """
    entry = _dict(_dict(_dict(config.get("mcp")).get("servers")).get(GATEWAY))
    return not is_engine_lazy_mcp(entry)


def _claude_problems(
    config: dict[str, Any],
    servers: list[str],
    agents: list[str],
    provider_missing: list[str] | None = None,
) -> list[str]:
    """What is wrong with the agents on Claude Code's CLI, in the words a person reads."""
    found: list[str] = []
    reachable = sorted([*_undeclared(config, agents, servers), *(provider_missing or [])])
    if reachable:
        found.append(t("still on the loopback server: {connectors}", connectors=", ".join(reachable)))
    # A gateway is the road for the connectors this agent is denied. With none mounted
    # there is nothing behind it to reach, and its absence costs the agent nothing.
    connectors = [server for server in servers if pattern_for(server)]
    if connectors and _gateway_missing(config):
        found.append(t("the gateway {gateway} is not mounted in OpenClaw, so its connectors reach no road",
                       gateway=GATEWAY))
    return found


def _agy_problems(
    config: dict[str, Any],
    servers: list[str],
    agents: list[str],
    provider_missing: list[str] | None = None,
) -> list[str]:
    """What is wrong with the agents on `agy`, whose only lost road is the loopback one."""
    reachable = sorted([*_undeclared(config, agents, servers), *(provider_missing or [])])
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
    return t("Run 'nexgen openclaw setup': it denies every connector on the loopback server and mounts the "
             "gateway in OpenClaw, which is where they load lazily.")


def _ok(count: int, has_agy: bool, providers: list[str] | None = None) -> str:
    """The line said when every agent judged keeps its connectors off the loopback."""
    if has_agy:
        base = t("The {count} agent(s) that read their connectors from their own CLI keep every connector "
                 "off the loopback server, and that CLI's configuration carries them.", count=count)
    else:
        base = t("The {count} agent(s) on Claude Code's CLI keep every connector off the loopback server and "
                 "reach them through the gateway in OpenClaw.", count=count)
    if not providers:
        return base
    return f"{base} {t('Model providers covered: {providers}.', providers=', '.join(providers))}"


def check_claude_loopback(
    home: Path,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
    platform: str | None = None,
) -> CheckOutcome | None:
    """One outcome for the whole question, or None when there is nothing here to judge."""
    path = openclaw_config(home)
    if not path.is_file():
        return None
    if detect_node_only(home, platform=platform):
        return node_only_outcome(CHECK_ID)
    config, _error = read_config(path)
    if config is None:
        return None
    servers = _servers(config)
    deny = loopback_deny(config, servers, home)
    prov_keys = routed_keys(config)
    claude_prov_keys = [k for k, r in prov_keys.items() if r == CLAUDE_CLI]
    agy_prov_keys = [k for k, r in prov_keys.items() if r == ANTIGRAVITY_CLI and deny.agy_connectors]

    has_claude = bool(deny.claude or claude_prov_keys)
    has_agy = bool(deny.agy or agy_prov_keys)
    if not has_claude and not has_agy:
        return None

    prov_missing = undeclared_provider_patterns(config, servers, deny.agy_connectors)
    claude_prov_missing = [m for m in prov_missing if any(m.startswith(f"{k}: ") for k in claude_prov_keys)]
    agy_prov_missing = [m for m in prov_missing if any(m.startswith(f"{k}: ") for k in agy_prov_keys)]

    problems: list[str] = []
    if has_claude:
        problems.extend(_claude_problems(config, servers, deny.claude, claude_prov_missing))
    if has_agy:
        problems.extend(_agy_problems(config, deny.agy_connectors, deny.agy, agy_prov_missing))

    detail = ", ".join(sorted(set([*deny.claude, *claude_prov_keys, *deny.agy, *agy_prov_keys])))
    if problems:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=_message(has_claude, has_agy, problems),
            action=_action(has_agy),
            detail=detail,
        )
    covered_providers = sorted(set([*claude_prov_keys, *agy_prov_keys]))
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=_ok(len(deny.claude) + len(deny.agy), has_agy, covered_providers),
        detail=detail,
    )
