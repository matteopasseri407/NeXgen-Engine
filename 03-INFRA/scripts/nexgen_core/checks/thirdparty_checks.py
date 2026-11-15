"""The doctor's one line about third-party updates: what waits for a yes, what needs a look.

One job: make the guardian's verdict visible where the owner already looks. Until this existed, the
verdict file was read only by `nexgen info` and the notifier lanes, so a machine could sit with eight
held updates for a year and `nexgen doctor` stayed green and silent.

It never fetches and never applies: it reads the two files the hourly watch leaves behind, and the
policies are the ones `thirdparty_status` derives, so this line and that command can never disagree.
It is a WARN at most: a pending update is not a broken machine and must never change the exit code.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core import extensions, thirdparty_status
from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity

#: How many names a single doctor line may carry before it becomes a paragraph.
NAMES_ON_A_LINE = 4


def _names(items: list[thirdparty_status.Item]) -> str:
    listed = ", ".join(item.name for item in items[:NAMES_ON_A_LINE])
    rest = len(items) - NAMES_ON_A_LINE
    return listed + (t(" and {count} more", count=rest) if rest > 0 else "")


def check_thirdparty_updates(vault: Path, home: Path, engine_root: Path, state_dir: Path) -> CheckOutcome:
    """How many third-party items are ready to move, and how many wait for a person."""
    data = extensions.collect(home=home, vault_data=vault, engine_root=engine_root, state_dir=state_dir)
    items = thirdparty_status.build(data)
    if not items:
        return CheckOutcome(
            id="thirdparty.updates",
            severity=Severity.OK,
            message=t("No third-party skill or MCP server is declared on this machine."),
        )
    ready = [item for item in items if item.policy in (thirdparty_status.AUTO, thirdparty_status.YES)]
    looking = [item for item in items if item.policy == thirdparty_status.LOOK]
    if ready:
        return CheckOutcome(
            id="thirdparty.updates",
            severity=Severity.WARN,
            message=t("Third-party updates cleared and waiting for one yes ({count}): {names}.",
                      count=len(ready), names=_names(ready)),
            action=t("Run '{command}' and answer yes: it raises every cleared pin at once.",
                     command=thirdparty_status.BUMP),
        )
    if looking:
        return CheckOutcome(
            id="thirdparty.updates",
            severity=Severity.WARN,
            message=t("Third-party updates that need a look first ({count}): {names}.",
                      count=len(looking), names=_names(looking)),
            action=t("Run 'nexgen thirdparty status': one line per item, with the exact thing that moves it."),
        )
    if data.get("upstream_checked_at") is None:
        return CheckOutcome(
            id="thirdparty.updates",
            severity=Severity.UNDETERMINED,
            message=t("Upstream versions have not been checked on this machine yet."),
            action=t("The hourly watch does it, or run '{command}' now.", command=thirdparty_status.BUMP),
        )
    return CheckOutcome(
        id="thirdparty.updates",
        severity=Severity.OK,
        message=t("No third-party update is waiting ({count} items watched).", count=len(items)),
    )
