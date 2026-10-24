"""How the doctor runs its checks: together, and inside a budget.

One job: take the checks the doctor registered, run the read-only ones at the
same time, and stop waiting when the budget is spent. It does not decide what a
check is, and it never drops one: a check that did not answer becomes a finding
of its own, whose action leads to the real answer.

The same module keeps the other two ways to run them, because the difference
between "read all of this" and "repair what can be repaired" belongs in one
place rather than in the caller:

- in order, applying remedies, for `--fix`: a repair must never race the checks
  that are judging the same machine;
- at the same time with a deadline, for `--summary`: the pre-push hook and the
  verification of a collection both run it, and both have a budget of their own.

Two checks declare the same `resource` when running them together is not safe:
two `git fetch` on one repository contend for its lock file and one of the two
fails for no reason. Those, and only those, take turns.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Report, Severity

#: How long `doctor --summary` waits before it reports what is still missing.
#: Chosen from the measurements, not from the air: the checks of a full home sum
#: to more than this, the slowest single one takes a couple of seconds, and the
#: whole run has to leave room inside the smoke's own budget.
SUMMARY_DEADLINE_SECONDS = 6.0


@dataclass(frozen=True)
class Check:
    """One registered check: its name, how to run it, and what it touches."""

    id: str
    produce: Callable[[], Any]
    #: Checks that name the same resource take turns. `None` means independent.
    resource: str | None = None


def _crashed(check_id: str, exc: Exception) -> CheckOutcome:
    """A check that raised is a finding, never the end of the report."""
    return CheckOutcome(
        id=f"{check_id}.crashed",
        severity=Severity.BROKEN,
        message=t(
            "The check '{check}' could not run: {error}",
            check=check_id, error=f"{type(exc).__name__}: {exc}",
        ),
        action=t("Run 'nexgen doctor --json' and look at this check; the rest of the report is still valid."),
    )


def _late(check_id: str, seconds: float) -> CheckOutcome:
    """A check the summary stopped waiting for, said out loud."""
    return CheckOutcome(
        id=f"{check_id}.late",
        severity=Severity.UNDETERMINED,
        message=t(
            "The check '{check}' had not answered after {seconds:.0f}s, so the summary went on without it.",
            check=check_id, seconds=seconds,
        ),
        action=t("Run 'nexgen doctor' without --summary: it waits for every check."),
    )


def _collect(report: Report, check: Check, produced: Any, seconds: float, *, apply_remedies: bool = False) -> None:
    """Append one check's outcomes in the order it produced them."""
    report.timings.append((check.id, seconds))
    if isinstance(produced, Exception):
        report.add(_crashed(check.id, produced))
        return
    for outcome in (produced if isinstance(produced, list) else [produced]):
        if outcome is not None:
            report.add(outcome, apply_remedy=apply_remedies)


def run_in_order(checks: Sequence[Check], *, apply_remedies: bool = False) -> Report:
    """One at a time, in the order they were registered (the `--fix` path)."""
    report = Report()
    for check in checks:
        started = time.perf_counter()
        try:
            produced: Any = check.produce()
        except Exception as exc:  # noqa: BLE001 - a damaged machine must still get a report
            produced = exc
        _collect(report, check, produced, time.perf_counter() - started, apply_remedies=apply_remedies)
    return report


def run(checks: Sequence[Check], *, deadline_seconds: float | None = None) -> Report:
    """Every check at once, and no waiting past the deadline.

    A check still running at the deadline keeps running in the background and
    the process leaves it behind: the checks only read, so an abandoned one
    cannot damage anything, and the alternative is the hook waiting for a
    network timeout it did not budget for.
    """
    report = Report()
    if not checks:
        return report

    started = time.monotonic()
    finished: dict[str, tuple[Any, float]] = {}
    locks = {
        resource: threading.Lock() for resource in {c.resource for c in checks if c.resource}
    }
    condition = threading.Condition()

    def worker(check: Check) -> None:
        began = time.perf_counter()
        try:
            lock = locks.get(check.resource) if check.resource else None
            if lock is None:
                produced: Any = check.produce()
            else:
                with lock:
                    produced = check.produce()
        except Exception as exc:  # noqa: BLE001 - a check that raises is a finding, not a crash
            produced = exc
        with condition:
            finished[check.id] = (produced, time.perf_counter() - began)
            condition.notify_all()

    threads = [
        threading.Thread(target=worker, args=(check,), name=f"doctor:{check.id}", daemon=True)
        for check in checks
    ]
    for thread in threads:
        thread.start()

    deadline = None if deadline_seconds is None else started + deadline_seconds
    while len(finished) < len(checks):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            break
        with condition:
            condition.wait(remaining if remaining is not None else 0.5)

    for check in checks:
        entry = finished.get(check.id)
        if entry is None:
            waited = time.monotonic() - started
            report.timings.append((check.id, waited))
            report.add(_late(check.id, waited))
            continue
        produced, seconds = entry
        _collect(report, check, produced, seconds)
    return report
