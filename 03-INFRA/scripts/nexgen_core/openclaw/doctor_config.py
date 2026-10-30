"""What the configuration in force switches on, judged without asking anything.

One job: the doctor's checks that read the OpenClaw file and nothing else. The
heartbeat that starts jobs writing to the channels continuously, a Google
subscription the terms do not allow, the ACP bridge that would carry one, the
tool-search mode a large connector list makes matter, and the engine's lazy-mcp
gateway an agent on `claude-cli` needs to reach its connectors.

They were part of `doctor.py` until that file reached its size ceiling. None of
them opens a socket, starts a process or writes a file, which is why they need
nothing the rest of the doctor brings with it.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.terms import google_subscriptions
from nexgen_core.report import CheckOutcome, Severity

#: Above this many mounted servers, the schemas Tool Search defers are worth saying out loud.
MANY_MCP_SERVERS_THRESHOLD = 4


def check_heartbeat(config: dict[str, Any]) -> CheckOutcome:
    """A live heartbeat starts system jobs that write on the channels continuously."""
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    heartbeat = defaults.get("heartbeat") if isinstance(defaults.get("heartbeat"), dict) else {}
    every = heartbeat.get("every")
    if every == "0m":
        return CheckOutcome(
            id="openclaw.heartbeat",
            severity=Severity.OK,
            message=t("The heartbeat is off, as the engine configures it."),
        )
    return CheckOutcome(
        id="openclaw.heartbeat",
        severity=Severity.WARN,
        message=t("The heartbeat runs every '{every}': it starts system jobs that write to the channels.",
                  every=every if isinstance(every, str) and every else t("(not set)")),
        action=t("Set agents.defaults.heartbeat.every to '0m'."),
    )


def check_google(config: dict[str, Any]) -> CheckOutcome:
    """Google plans may not be used outside Google's own clients; an API key may."""
    forbidden = google_subscriptions(config)
    if forbidden:
        return CheckOutcome(
            id="openclaw.plugins.google",
            severity=Severity.BROKEN,
            message=t("OpenClaw is configured with a Google subscription, which its terms do not allow: {names}",
                      names=", ".join(forbidden)),
            action=t("Remove it: use Claude, Codex, the OpenCode Go key or a local model."),
        )
    return CheckOutcome(
        id="openclaw.plugins.google",
        severity=Severity.OK,
        message=t("No Google subscription is configured in OpenClaw."),
    )


def check_acp(config: dict[str, Any]) -> CheckOutcome:
    """A Gemini seat through ACP is allowed by the protocol and not by the terms."""
    acp = config.get("acp") if isinstance(config.get("acp"), dict) else {}
    allowed = acp.get("allowedAgents") if isinstance(acp.get("allowedAgents"), list) else []
    if any(str(agent).lower() == "gemini" for agent in allowed):
        return CheckOutcome(
            id="openclaw.acp.gemini",
            severity=Severity.WARN,
            message=t("The ACP bridge allows the 'gemini' agent; its terms do not allow it outside Google's clients."),
            action=t("Remove 'gemini' from acp.allowedAgents."),
        )
    return CheckOutcome(
        id="openclaw.acp.gemini",
        severity=Severity.OK,
        message=t("The ACP bridge does not allow a Google agent."),
    )


def check_tool_search(config: dict[str, Any]) -> CheckOutcome:
    """Tool Search: active by default in OpenClaw; warned if disabled with many MCP servers."""
    tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    ts = tools.get("toolSearch")
    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    servers = mcp.get("servers") if isinstance(mcp.get("servers"), dict) else {}
    mounted_count = sum(1 for s in servers.values() if isinstance(s, dict) and s.get("enabled", True) is not False)

    if ts is False:
        return CheckOutcome(
            id="openclaw.tool_search",
            severity=Severity.WARN,
            message=t(
                "Tool Search is disabled by a legacy engine setting; next setup will remove it.",
            ),
            action=t("Run 'nexgen openclaw setup' to retire the legacy setting and restore native Tool Search."),
        )

    if isinstance(ts, dict) and ts.get("enabled") is False:
        if mounted_count >= MANY_MCP_SERVERS_THRESHOLD:
            return CheckOutcome(
                id="openclaw.tool_search",
                severity=Severity.WARN,
                message=t(
                    "Tool Search is disabled by user configuration with {count} MCP servers mounted: direct schemas add large overhead to every prompt.",
                    count=mounted_count,
                ),
                action=t("Remove 'tools.toolSearch' from openclaw.json to restore native Tool Search."),
            )
        return CheckOutcome(
            id="openclaw.tool_search",
            severity=Severity.OK,
            message=t(
                "Tool Search is disabled by user configuration ({count} MCP servers mounted, direct schemas in use).",
                count=mounted_count,
            ),
        )

    mode = t("default") if ts is None else (str(ts.get("mode")) if isinstance(ts, dict) and ts.get("mode") else t("configured"))
    return CheckOutcome(
        id="openclaw.tool_search",
        severity=Severity.OK,
        message=t(
            "Tool Search is active ({mode}): MCP and runtime tools are deferred behind search and call.",
            mode=mode,
        ),
    )


def check_mcp_lazy(config: dict[str, Any]) -> CheckOutcome:
    """The lazy-mcp gateway: the road a `claude-cli` agent's connectors take.

    Those agents keep every connector off OpenClaw's `alwaysLoad` loopback server, so
    the engine's entry under `mcp.servers` is what makes the connectors reachable at
    all. Without such an agent nothing is behind the gateway and its absence is fine.
    """
    from nexgen_core.mcp_render.openclaw import is_engine_lazy_mcp
    from nexgen_core.openclaw.claude_cli import claude_cli_agents

    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    servers = mcp.get("servers") if isinstance(mcp.get("servers"), dict) else {}
    entry = servers.get("lazy-mcp")
    if entry is not None and is_engine_lazy_mcp(entry):
        return CheckOutcome(
            id="openclaw.mcp.lazy",
            severity=Severity.OK,
            message=t("The lazy-mcp gateway is mounted in OpenClaw: an agent on 'claude-cli' reaches its "
                      "connectors through it."),
        )
    if entry is None:
        if claude_cli_agents(config):
            return CheckOutcome(
                id="openclaw.mcp.lazy",
                severity=Severity.BROKEN,
                message=t("The lazy-mcp gateway is not mounted in OpenClaw, and an agent runs on 'claude-cli': "
                          "its connectors are denied on the loopback server and reach it nowhere."),
                action=t("Run 'nexgen openclaw setup' or 'nexgen mcp render': it mounts the gateway, which is the "
                         "road those connectors take."),
            )
        return CheckOutcome(
            id="openclaw.mcp.lazy",
            severity=Severity.OK,
            message=t("No lazy-mcp gateway is mounted in OpenClaw (connectors are direct)."),
        )
    return CheckOutcome(
        id="openclaw.mcp.lazy",
        severity=Severity.WARN,
        message=t("A custom 'lazy-mcp' connector is configured under mcp.servers; OpenClaw does not use the engine gateway."),
        action=t("Verify if this custom connector is needed or rename it to avoid collision with the engine gateway."),
    )
