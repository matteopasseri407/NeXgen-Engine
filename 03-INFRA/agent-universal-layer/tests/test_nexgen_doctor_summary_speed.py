"""The summary keeps its budget by moving, not by checking less.

`doctor --summary` runs in the pre-push hook and in the verification of a
collection, so its duration is somebody else's budget. The way to keep it is
not to skip checks: it is to ask the independent ones at the same time and to
stop waiting when the budget is spent, saying which checks are still missing.

Every test here uses fake checks: no home, no command, no network.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import doctor as doctor_mod  # noqa: E402
from nexgen_core.checks import schedule  # noqa: E402
from nexgen_core.report import CheckOutcome, Severity  # noqa: E402


def _ok(check_id: str) -> CheckOutcome:
    return CheckOutcome(id=check_id, severity=Severity.OK, message=f"{check_id} is fine")


def _slow(seconds: float, check_id: str = "slow"):
    def produce():
        time.sleep(seconds)
        return _ok(check_id)
    return produce


def test_the_independent_checks_run_at_the_same_time():
    """Four checks that each wait 0.2s cost 0.2s, not 0.8s."""
    checks = [schedule.Check(f"c{index}", _slow(0.2, f"c{index}")) for index in range(4)]

    started = time.monotonic()
    report = schedule.run(checks, deadline_seconds=5.0)
    spent = time.monotonic() - started

    assert [o.id for o in report.outcomes] == ["c0", "c1", "c2", "c3"]
    assert spent < 0.6, spent


def test_a_check_past_the_deadline_is_reported_missing_never_dropped():
    checks = [
        schedule.Check("fast-a", _slow(0.01, "fast-a")),
        schedule.Check("slow", _slow(6.0, "slow")),
        schedule.Check("fast-b", _slow(0.01, "fast-b")),
    ]

    # A loaded Windows runner needs most of a second to start three threads: the
    # deadline leaves room for the fast checks and still cuts the slow one short.
    started = time.monotonic()
    report = schedule.run(checks, deadline_seconds=1.0)
    spent = time.monotonic() - started

    assert spent < 4.0, spent
    ids = [o.id for o in report.outcomes]
    assert ids == ["fast-a", "slow.late", "fast-b"], ids
    late = report.outcomes[1]
    assert late.severity == Severity.UNDETERMINED
    assert "slow" in late.message
    assert late.action and "nexgen doctor" in late.action
    # every registered check is in the timing table, answered or not
    assert [check_id for check_id, _ in report.timings] == ["fast-a", "slow", "fast-b"]


def test_checks_that_name_the_same_resource_take_turns():
    """Two `git fetch` on one repository fight over its lock: never together."""
    entries: list[str] = []
    opened = threading.Lock()

    def produce(name: str):
        def inner():
            with opened:
                entries.append(f"enter {name}")
            time.sleep(0.15)
            with opened:
                entries.append(f"leave {name}")
            return _ok(name)
        return inner

    checks = [
        schedule.Check("first", produce("first"), "one-repository"),
        schedule.Check("second", produce("second"), "one-repository"),
    ]

    started = time.monotonic()
    schedule.run(checks, deadline_seconds=5.0)
    spent = time.monotonic() - started

    # Interleaved entries are the failure this pins: two `git fetch` on one
    # repository contend for its lock file and one of them fails for no reason.
    assert entries in (
        ["enter first", "leave first", "enter second", "leave second"],
        ["enter second", "leave second", "enter first", "leave first"],
    ), entries
    assert spent >= 0.2, spent


def test_a_check_that_raises_is_a_finding_and_the_others_still_answer():
    def explode():
        raise RuntimeError("a damaged manifest")

    checks = [schedule.Check("broken", explode), schedule.Check("fine", _slow(0.01, "fine"))]

    for report in (schedule.run(checks, deadline_seconds=5.0), schedule.run_in_order(checks)):
        by_id = {outcome.id: outcome for outcome in report.outcomes}
        assert by_id["broken.crashed"].severity == Severity.BROKEN
        assert "a damaged manifest" in by_id["broken.crashed"].message
        assert by_id["fine"].severity == Severity.OK


def test_the_repair_path_runs_in_order_and_applies_the_remedies():
    """`--fix` keeps the old behaviour: one at a time, remedy applied."""
    repaired: list[str] = []

    def broken():
        return CheckOutcome(
            id="fixable",
            severity=Severity.BROKEN,
            message="something to repair",
            remedy=lambda: repaired.append("ran") or True,
        )

    report = schedule.run_in_order([schedule.Check("fixable", broken)], apply_remedies=True)

    assert repaired == ["ran"]
    assert report.outcomes[0].remedied is True
    assert report.outcomes[0].severity == Severity.OK


def test_the_deadline_belongs_to_the_summary_and_never_to_a_repair():
    assert doctor_mod.summary_budget(summary=True, fix=False) == schedule.SUMMARY_DEADLINE_SECONDS
    assert doctor_mod.summary_budget(summary=False, fix=False) is None
    assert doctor_mod.summary_budget(summary=True, fix=True) is None
    assert doctor_mod.summary_budget(summary=False, fix=True) is None


def test_the_summary_deadline_leaves_room_inside_the_smoke_budget():
    """The smoke runs this command inside 30s together with seven other starts."""
    assert 0 < schedule.SUMMARY_DEADLINE_SECONDS <= 8.0
