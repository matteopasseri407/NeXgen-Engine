"""What the configuration in force switches on, judged without asking anything.

One job: the doctor's checks that read the OpenClaw file and nothing else. The
proactive check that turns a bot into a metered turn every half hour, the engine's
own hourly context guard as the scheduler holds it, a Google subscription the terms
do not allow, the ACP bridge that would carry one, the tool-search mode a large
connector list makes matter, and the engine's lazy-mcp gateway an agent on
`claude-cli` needs to reach its connectors. It also says where OpenClaw looks for
skills, because a state directory of its own hides the shared root the engine
writes into.

They were part of `doctor.py` until that file reached its size ceiling. None of
them opens a socket, starts a process or writes a file, which is why they need
nothing the rest of the doctor brings with it. The one question that needs the
scheduler -- whether an automation job runs the proactive check, and how the
context guard is declared -- arrives as a value the caller read, and a caller that
read nothing says so.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.terms import google_subscriptions
from nexgen_core.report import CheckOutcome, Severity

#: Above this many mounted servers, the schemas Tool Search defers are worth saying out loud.
MANY_MCP_SERVERS_THRESHOLD = 4


def _legacy_heartbeat_keys(config: dict[str, Any]) -> list[str]:
    """The retired heartbeat settings still in this file, by path.

    OpenClaw reads none of them any more: a heartbeat is an automation job and Doctor
    removes these keys once it has converted the monitor. A file that still carries one
    is a machine whose jobs may not exist yet, so what it schedules is not something the
    job list can answer.
    """
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    found: list[str] = []
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    if isinstance(defaults.get("heartbeat"), dict) and defaults["heartbeat"]:
        found.append("agents.defaults.heartbeat")
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    for agent_id, entry in sorted(entries.items()):
        heartbeat = entry.get("heartbeat") if isinstance(entry, dict) else None
        if isinstance(heartbeat, dict) and heartbeat:
            found.append(f"agents.entries.{agent_id}.heartbeat")
    return found


def check_heartbeat(
    config: dict[str, Any], jobs: list[dict[str, Any]] | None = None,
) -> CheckOutcome:
    """The proactive check, judged on the automation job that schedules it.

    `jobs` is what the scheduler listed, or None when nobody asked it. This check never
    starts a process itself (the doctor's contract), so a caller that did not ask says
    so: an unread job list is "unknown", never the green line the retired
    `agents.defaults.heartbeat` key used to earn while a job fired every half hour.
    What a running check costs is `_running_check`'s question.
    """
    from nexgen_core.openclaw.automations import enabled_heartbeat_jobs, heartbeat_jobs, is_enabled

    retained = _legacy_heartbeat_keys(config)
    if retained:
        return CheckOutcome(
            id="openclaw.heartbeat",
            severity=Severity.WARN,
            message=t("The retired heartbeat settings are still in this file ({paths}): OpenClaw schedules "
                      "heartbeats as automation jobs and its doctor converts these keys into them.",
                      paths=", ".join(retained)),
            action=t("Run 'openclaw doctor --fix' to convert them, then 'nexgen openclaw setup' to converge "
                     "the migrated job."),
        )
    if jobs is None:
        return CheckOutcome(
            id="openclaw.heartbeat",
            severity=Severity.UNDETERMINED,
            message=t("Unknown: nothing here asked the scheduler, so whether a proactive check runs is unread."),
            action=t("Run 'nexgen openclaw doctor' (which asks the scheduler) or 'openclaw automations list --all'."),
        )
    running = enabled_heartbeat_jobs(jobs)
    if running:
        return _running_check(running)
    switched_off = [job for job in heartbeat_jobs(jobs) if not is_enabled(job)]
    detail = t(" ({count} disabled job matches)", count=len(switched_off)) if switched_off else ""
    return CheckOutcome(
        id="openclaw.heartbeat",
        severity=Severity.OK,
        message=t("No enabled automation job runs a proactive check{detail}.", detail=detail),
    )


def _running_check(running: list[dict[str, Any]]) -> CheckOutcome:
    """What a check that actually runs costs, which is three facts about its payload.

    Whether it runs in the agent's own conversation, whether it carries the workspace
    bootstrap files, and whether it can call tools. Any of those makes a run a model call
    over everything the conversation has grown to, and the squad's own prompt measured
    78,717 tokens for one (docs/proposals/daru-burn.md). A light job is still a model
    call, so a cadence faster than four runs a day is named as well, and only a light job
    at that pace is a pass.
    """
    from nexgen_core.openclaw.automations import (
        LIGHT_MAX_MINUTES,
        cadence_minutes,
        job_id,
        job_name,
        runs_light,
        runs_per_day,
        schedule_text,
    )

    expensive = [job for job in running if not runs_light(job)]
    if expensive:
        described = ", ".join(f"{job_name(job)} every {schedule_text(job)} ({job_id(job)})" for job in expensive)
        return CheckOutcome(
            id="openclaw.heartbeat",
            severity=Severity.WARN,
            message=t("An automation job runs the proactive check over the agent's own conversation: {jobs}. "
                      "A run like that is one model call over everything that conversation holds, 78,717 "
                      "tokens measured on this squad, at {count} runs a day.",
                      jobs=described, count=_runs_a_day(expensive, runs_per_day)),
            action=t("Run 'nexgen openclaw setup': it converges the job to its own session, without the "
                     "bootstrap files and without tools, every 6 hours. A full check stays an opt-in in "
                     "squad.yaml (heartbeat: mode: full)."),
        )
    frequent = [
        job for job in running
        if (cadence := cadence_minutes(job)) is not None and cadence < LIGHT_MAX_MINUTES
    ]
    if frequent:
        described = ", ".join(f"{job_name(job)} every {schedule_text(job)}" for job in frequent)
        return CheckOutcome(
            id="openclaw.heartbeat",
            severity=Severity.WARN,
            message=t("The proactive check is light, but it fires more often than four times a day: {jobs}. "
                      "Every run is still a model call.", jobs=described),
            action=t("Run 'nexgen openclaw setup' to converge it to every 6 hours, or set "
                     "'heartbeat: every:' in squad.yaml to the cadence you want."),
        )
    described = ", ".join(f"{job_name(job)} every {schedule_text(job)}" for job in running)
    return CheckOutcome(
        id="openclaw.heartbeat",
        severity=Severity.OK,
        message=t("The proactive check runs light: {jobs}, in its own session, without the bootstrap files "
                  "and without tools.", jobs=described),
    )


def _runs_a_day(jobs: list[dict[str, Any]], per_day) -> str:
    """How many model calls a day these jobs cost, as a number when the cadences are read."""
    counts = [count for job in jobs if (count := per_day(job)) is not None]
    if len(counts) == len(jobs) and counts:
        return str(sum(counts))
    return t("an unknown number of")


def check_context_guard(jobs: list[dict[str, Any]] | None) -> CheckOutcome | None:
    """The hourly context guard, judged on the job the scheduler actually holds.

    `setup` registers the job with a declaration key and, with `--no-deliver`, a
    delivery mode of `none`; without that flag the gateway records every hourly run as a
    failed delivery while the doctor stays green. `jobs` is what the scheduler listed, or
    None when nobody asked it: an unread scheduler is not a finding, because the
    heartbeat check already says the list was never read.
    """
    from nexgen_core.openclaw.automations import (
        CONTEXT_GUARD_DECLARATION_KEY,
        context_guard_jobs,
        delivery_mode,
        is_enabled,
        job_name,
    )

    if jobs is None:
        return None
    guard = context_guard_jobs(jobs)
    if not guard:
        return CheckOutcome(
            id="openclaw.cron.context_guard",
            severity=Severity.WARN,
            message=t("No automation job carries the engine's context guard declaration "
                      "({key}): no chat is compacted by it.", key=CONTEXT_GUARD_DECLARATION_KEY),
            action=t("Run 'nexgen openclaw setup' with the gateway up. A person who wants no "
                     "compaction at all switches it off with NEXGEN_OPENCLAW_MAX_COMPACTIONS=0."),
        )
    faults: list[str] = []
    for job in guard:
        name = job_name(job)
        if not is_enabled(job):
            faults.append(t("'{name}' is switched off", name=name))
        mode = delivery_mode(job)
        if mode != "none":
            faults.append(t("'{name}' delivers every run ({mode})", name=name, mode=mode or t("unspecified")))
    if faults:
        return CheckOutcome(
            id="openclaw.cron.context_guard",
            severity=Severity.WARN,
            message=t("The context guard job is not as the engine declares it: {faults}.",
                      faults="; ".join(faults)),
            action=t("Run 'nexgen openclaw setup' with the gateway up: it re-declares the job with "
                     "--no-deliver, and OpenClaw patches the one the same declaration key names."),
        )
    return CheckOutcome(
        id="openclaw.cron.context_guard",
        severity=Severity.OK,
        message=t("The hourly context guard job is registered and records no delivery."),
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


def check_skill_root(
    config: dict[str, Any], env: Mapping[str, str], home: Path,
) -> CheckOutcome | None:
    """Where OpenClaw looks for skills, against the folder the engine writes them into.

    The engine's views land in the shared `~/.agents/skills`, which OpenClaw
    discovers as its own "personal agent skills" root only while its state
    directory is the default one. Its documentation (`docs.openclaw.ai/tools/skills`)
    says that when `OPENCLAW_STATE_DIR` points somewhere else, session skill
    indexes exclude home-scoped personal roots: every skill the engine
    materialises stays invisible to the bot, and nothing anywhere reports it.
    A node is not asked, because its agents run on the gateway and that machine's
    state directory is the one that decides.
    """
    gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
    mode = gateway.get("mode")
    if isinstance(mode, str) and mode.strip() == "remote":
        return None
    raw = str(env.get("OPENCLAW_STATE_DIR") or "").strip()
    default = home / ".openclaw"
    if not raw:
        return CheckOutcome(
            id="openclaw.skills.root",
            severity=Severity.OK,
            message=t("OpenClaw keeps its state in {default}, so it reads the shared ~/.agents/skills the "
                      "engine materialises its views into.", default=default),
        )
    configured = _configured_state_dir(raw, home)
    try:
        same = configured.resolve() == default.resolve()
    except OSError:  # nexgen: allow reason="a folder that cannot be resolved cannot be the default one, and the line then reports the path as it is set"
        same = False
    if same:
        return CheckOutcome(
            id="openclaw.skills.root",
            severity=Severity.OK,
            message=t("OPENCLAW_STATE_DIR is set to the default folder ({configured}), so the shared "
                      "~/.agents/skills is still read.", configured=configured),
        )
    return CheckOutcome(
        id="openclaw.skills.root",
        severity=Severity.BROKEN,
        message=t("OPENCLAW_STATE_DIR points at {configured}, and OpenClaw then leaves the shared "
                  "~/.agents/skills out of its skill indexes: every skill the engine materialises is "
                  "invisible to the bot, with no error anywhere.", configured=configured),
        action=t("Unset OPENCLAW_STATE_DIR where the gateway is started, so OpenClaw reads {default} and "
                 "the shared skills with it. A state directory that must stay where it is needs the skills "
                 "beside it, under {configured}/skills.", default=default, configured=configured),
    )


def _configured_state_dir(raw: str, home: Path) -> Path:
    """The folder `OPENCLAW_STATE_DIR` names, with `~` expanded against this home.

    Expanded by hand, like `org_block._openclaw_path`: this check must answer
    about the home it was given, and `expanduser` would answer about the
    environment's instead.
    """
    if raw == "~":
        return home
    if raw.startswith(("~/", "~\\")):
        return home / raw[2:]
    return Path(raw)
