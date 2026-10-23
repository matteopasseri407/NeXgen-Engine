"""The doctor's quota line: informative, never red, and never slow.

The check is asserted on its own outcome and once through the real doctor on a
sandbox, because the wiring is what a refactor breaks.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import run_agent_doctor, Sandbox

from nexgen_core import i18n
from nexgen_core.checks.quota_checks import check_quota
from nexgen_core.report import Severity


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: the message is asserted below."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def codex_reading(home: Path, percent: float) -> None:
    """A Codex session carrying one rate-limit line, written a minute ago.

    A reading older than its window is stale and the report drops the
    percentage, so a fixture with a frozen moment would test the wrong thing.
    """
    folder = home / ".codex" / "sessions" / "2026" / "10" / "23"
    folder.mkdir(parents=True, exist_ok=True)
    moment = datetime.now(timezone.utc) - timedelta(minutes=1)
    resets = int(moment.timestamp()) + 3600  # an hour after the reading, computed here
    (folder / "rollout-2026-10-23.jsonl").write_text(json.dumps({
        "timestamp": moment.isoformat().replace("+00:00", "Z"),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": percent, "window_minutes": 720, "resets_at": resets},
                "secondary": None,
                "credits": {"has_credits": False, "balance": None},
                "plan_type": "free",
            },
        },
    }) + "\n", encoding="utf-8")


def test_a_machine_with_no_readable_percentage_says_exactly_that(tmp_path):
    outcome = check_quota(tmp_path / "home", tmp_path / "vault")

    assert outcome.id == "quota.known"
    assert outcome.severity == Severity.OK
    assert outcome.message == "No provider's percentage is readable on this machine."
    assert outcome.action is None


def test_the_line_carries_the_percentages_that_are_known(tmp_path):
    codex_reading(tmp_path, 59.0)

    outcome = check_quota(tmp_path, tmp_path / "vault")

    assert outcome.severity == Severity.OK
    assert outcome.message == "Known quotas: Codex (free) 59% (12h)"


def test_a_plan_near_its_ceiling_is_a_warning_and_never_a_failure(tmp_path):
    codex_reading(tmp_path, 86.0)

    outcome = check_quota(tmp_path, tmp_path / "vault")

    assert outcome.severity == Severity.WARN
    assert outcome.message == "Known quotas: Codex (free) 86% (12h)"
    assert outcome.action == "Run 'nexgen quota' to see every window."


def test_the_threshold_is_judged_on_the_number_and_not_on_the_printed_form(tmp_path):
    """79.6% is below the alert line, and it is shown as 79.6% rather than as 80%."""
    codex_reading(tmp_path, 79.6)

    below = check_quota(tmp_path, tmp_path / "vault")
    codex_reading(tmp_path, 80.4)
    above = check_quota(tmp_path, tmp_path / "vault")

    assert below.severity == Severity.OK
    assert below.message.endswith("79.6% (12h)")
    assert above.severity == Severity.WARN
    assert above.message.endswith("80.4% (12h)")


def test_the_doctor_prints_the_line_and_stays_aligned(sandbox: Sandbox):
    """The provider, window and percentage read the same in every language."""
    codex_reading(sandbox.home, 59.0)

    done = run_agent_doctor(sandbox)

    assert "[OK] quota.known:" in done.stdout
    assert "Codex (free) 59% (12h)" in done.stdout


def test_the_doctor_does_not_turn_a_full_plan_into_a_broken_machine(sandbox: Sandbox):
    codex_reading(sandbox.home, 99.0)

    done = run_agent_doctor(sandbox, "--json")

    report = json.loads(done.stdout)
    assert any(row["id"] == "quota.known" for row in report["warnings"])
    assert not any(row["id"] == "quota.known" for row in report["broken"])
