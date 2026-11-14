"""OpenClaw cron job registration and convergence for NeXgen Engine.

nexgen: one-job reason="declare and converge OpenClaw cron jobs with proper delivery modes"
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from nexgen_core.i18n import t

if TYPE_CHECKING:
    from nexgen_core.openclaw.setup_steps import Setup


def _openclaw_bin(setup: Setup) -> str:
    """The resolved command, or the bare name the shell would have to find."""
    return setup.which("openclaw") or "openclaw"


def groom_job_step(setup: Setup) -> None:
    """Registers the hourly groom as an OpenClaw command job, once.

    nexgen: one-job reason="declare and converge the hourly context-guard groom cron job in OpenClaw with no-deliver"
    The job keeps every agent chat small: idle chats are compacted, and a chat
    that grows past the limit is compacted even while in use. Without the job
    the policy in the config is only a promise.
    """
    if setup.dry_run:
        setup.out(t("  Context guard: skipped in dry-run."))
        return
    engine = setup.which("nexgen")
    if not engine:
        setup.out(t("  Context guard: 'nexgen' is not on PATH, job not registered."))
        return
    result = setup.run([
        _openclaw_bin(setup), "cron", "add", "nexgen-context-guard",
        "--cron", "0 * * * *",
        "--command", f"{engine} openclaw groom",
        "--declaration-key", "nexgen:context-guard",
        "--display-name", "Context guard",
        "--no-deliver",
    ])
    if result.returncode == 0:
        setup.out(t("  Context guard: hourly job registered."))
        return
    setup.out(t("  Context guard: the job was not registered, the gateway refused it."))
