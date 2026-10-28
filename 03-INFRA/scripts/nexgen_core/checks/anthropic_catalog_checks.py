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
is the same rule the writer applies), so the action is one command and no argument.

The service reads its own copy, `gateway.systemd.env`, so both files are judged: a
clean `.env` with a stale copy beside it keeps the fault alive.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.openclaw.claude_cli import claude_cli_agents
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.setup_service_env import ANTHROPIC_API_KEY, retired_environment_keys
from nexgen_core.paths import openclaw_config
from nexgen_core.report import CheckOutcome, Severity

#: The id a person reads, in the same family as the rest of the harness checks.
CHECK_ID = "openclaw.anthropic_catalog"

#: The gateway's environment as OpenClaw reads it, in the state directory beside its
#: configuration: the file the setup writes, and the copy the systemd unit carries.
ENVIRONMENT_FILES = (".env", "gateway.systemd.env")


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


def check_anthropic_catalog(home: Path) -> CheckOutcome | None:
    """One outcome for the whole question, or None when there is nothing here to judge."""
    config_path = openclaw_config(home)
    if not config_path.is_file():
        return None
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
        action=t("Run 'nexgen openclaw setup'."),
        detail=", ".join(agents),
    )
