"""The doctor's one line about quotas: the percentages this machine can read.

One job: report what the cheap sources know, so the doctor answers in the same
second on a machine with a week of transcripts behind it. It is informative and
never red: a plan close to its ceiling is a fact to act on, not a broken
machine, and the doctor's exit code must not change because of it.

A percentage above the first alert threshold makes the line a warning, so it
appears exactly where it matters and stays out of the way everywhere else.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core import quota
from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity


def check_quota(home: Path, vault: Path) -> CheckOutcome:
    """One line: every percentage that is a real number, and nothing invented."""
    rows, _notes = quota.percentage_rows(home=home, vault=vault)
    known = [row for row in rows if row.percent is not None]
    if not known:
        return CheckOutcome(
            id="quota.known",
            severity=Severity.OK,
            message=t("No provider's percentage is readable on this machine."),
        )
    listed = ", ".join(
        f"{row.provider} {quota.percent_text(row.percent)} ({row.window})" for row in known
    )
    # The threshold is judged on the number, never on the printed form: a plan
    # at 79.6% is below the alert line, and the line says 79.6%.
    highest = max((row.percent or 0.0) for row in known)
    near = highest >= quota.ALERT_AT[0]
    return CheckOutcome(
        id="quota.known",
        severity=Severity.WARN if near else Severity.OK,
        message=t("Known quotas: {known}", known=listed),
        action=t("Run 'nexgen quota' to see every window.") if near else None,
    )
