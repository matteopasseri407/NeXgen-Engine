"""Doctor check for OpenClaw agent folders with no configuration entry.

nexgen: one-job reason="warn when OpenClaw state has agent folders not declared in agents.entries without modifying them"
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity


def check_unconfigured_agents(config: dict[str, Any] | None, state_dir: Path) -> CheckOutcome | None:
    """Names agent folders in state_dir/agents that have no entry under agents.entries.

    nexgen: one-job reason="detect unconfigured agent folders and suggest human action without altering filesystem"
    Silent when the agents directory does not exist or when every agent folder is configured.
    """
    agents_dir = state_dir / "agents"
    if not agents_dir.is_dir():
        return None

    agents_sec = config.get("agents") if isinstance(config, dict) else {}
    entries = agents_sec.get("entries") if isinstance(agents_sec, dict) else {}
    configured = set(entries.keys()) if isinstance(entries, dict) else set()

    try:
        folders = [p.name for p in agents_dir.iterdir() if p.is_dir()]
    except OSError:  # nexgen: allow reason="unreadable or locked agents directory reports no unconfigured folders"
        return None

    unconfigured = sorted(f for f in folders if f not in configured)
    if not unconfigured:
        return None

    return CheckOutcome(
        id="openclaw.unconfigured_agents",
        severity=Severity.WARN,
        message=t("Agent folders have no entry in configuration: {names}.", names=", ".join(unconfigured)),
        action=t(
            "Remove the unused folders from {path} or declare them under agents.entries in openclaw.json.",
            path=str(agents_dir),
        ),
    )
