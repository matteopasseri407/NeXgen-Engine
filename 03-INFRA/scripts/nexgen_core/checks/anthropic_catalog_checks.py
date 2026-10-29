"""The Anthropic model catalogue, judged for a gateway whose agents run on `claude-cli`.

One job: read the configuration and the gateway's two environment sources, and say
whether a key no agent reads is sitting where OpenClaw's live discovery will send it
to `GET /v1/models`. Silence when the machine has no OpenClaw, no agent on
`claude-cli`, or a configuration this engine cannot read: those are other checks'
answers, not this one's.

A key left there is the whole of the bug a person sees: a workspace-scoped key
answers 400, the provider is recorded `unavailable`, `models.list` comes back with
`refreshFailed: true`, and the apps that read it (Android included) say they cannot
update the models on every open. The setup removes the key (`retired_environment_keys`
is the same rule the writer applies), so the fix is the setup and a restart, not a
service reinstall.

The systemd unit OpenClaw generates loads a copy of its own, `gateway.systemd.env`,
through `EnvironmentFile=`, so both files are judged: a clean `.env` with a stale copy
beside it keeps the fault alive. The engine never writes that copy, and a forced
service install is not the way to clear it: it rebuilds the unit from the invoking
shell and erases the variables the service carried (seven of them, measured on the
owner's gateway). The action names the file and leaves the one line to the person.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.openclaw.claude_cli import claude_cli_agents
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.node_only import detect_node_only, node_only_outcome
from nexgen_core.openclaw.setup_service_env import ANTHROPIC_API_KEY, retired_environment_keys
from nexgen_core.paths import openclaw_config
from nexgen_core.report import CheckOutcome, Severity

#: The id a person reads, in the same family as the rest of the harness checks.
CHECK_ID = "openclaw.anthropic_catalog"

#: The gateway's environment as OpenClaw reads it, in the state directory beside its
#: configuration: the file the setup writes, and the copy the systemd unit carries.
SERVICE_ENV_COPY = "gateway.systemd.env"
ENVIRONMENT_FILES = (".env", SERVICE_ENV_COPY)


def _carries_key(path: Path, key: str) -> bool:
    """Whether an environment file names this key; its value is never read out."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:  # nexgen: allow reason="a file that cannot be read carries no key for this check; the setup reports the write it could not do"
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            if stripped.split("=", 1)[0].strip() == key:
                return True
    return False


def check_anthropic_catalog(home: Path, platform: str | None = None) -> CheckOutcome | None:
    """One outcome for the whole question, or None when there is nothing here to judge."""
    config_path = openclaw_config(home)
    if not config_path.is_file():
        return None
    if detect_node_only(home, platform=platform):
        return node_only_outcome(CHECK_ID)
    config, _error = read_config(config_path)
    if config is None:
        return None
    agents = claude_cli_agents(config)
    if not agents:
        return None
    retired = retired_environment_keys(config, config_path)
    if ANTHROPIC_API_KEY not in retired:
        return None
    state_dir = config_path.parent
    offenders = [name for name in ENVIRONMENT_FILES if _carries_key(state_dir / name, ANTHROPIC_API_KEY)]
    if not offenders:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.OK,
            message=t("No ANTHROPIC_API_KEY sits in the gateway's environment: the {count} agent(s) on Claude "
                      "Code's CLI add nothing the model catalogue has to fail over.",
                      count=len(agents)),
            detail=", ".join(agents),
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.WARN,
        message=t("The apps that read the gateway (Android included) say they cannot update the models, because "
                  "Anthropic's catalogue discovery fails: ANTHROPIC_API_KEY is in {paths}, and no agent reads it.",
                  paths=", ".join(offenders)),
        action=_action(state_dir, offenders),
        detail=", ".join(agents),
    )


def _action(state_dir: Path, offenders: list[str]) -> str:
    """The commands that clear the key, and the one file a person has to clear by hand.

    The setup rewrites `.env` without the key, and the restart is what makes the gateway
    read the file again: the setup restarts the process only when the document changed.
    The unit's own copy is named with its path because the engine never writes it:
    removing that one line is the person's job, and it is the only non-destructive way
    to clear it. A forced service install is never the answer.
    """
    env_file = state_dir / ".env"
    if SERVICE_ENV_COPY in offenders:
        return t("Run 'nexgen openclaw setup', then 'nexgen openclaw restart', as the gateway's user: the setup "
                 "rewrites {env} without the key. The unit loads its own copy at {copy}, and the engine never writes "
                 "that file: delete the line that names {key} there before the restart. Never reinstall the service "
                 "to refresh it: a reinstall erases the service's other environment variables.",
                 env=env_file, copy=state_dir / SERVICE_ENV_COPY, key=ANTHROPIC_API_KEY)
    return t("Run 'nexgen openclaw setup', then 'nexgen openclaw restart', as the gateway's user: the setup "
             "rewrites {env} without the key, and the gateway reads that file when it starts. Never reinstall the "
             "service to refresh it: a reinstall erases the service's other environment variables.",
             env=env_file)
