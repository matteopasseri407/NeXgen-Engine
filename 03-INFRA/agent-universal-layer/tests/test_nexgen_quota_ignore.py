"""A provider this machine does not use: its rows leave every surface, one line says so.

The optional backend is replaced by a stub that answers for a Copilot plan at 100%
— the plan nobody here uses, and the one that used to raise an alert every four
hours — and the megaphone by a recorder. What is under test is where a row is
taken out, never the binary and never a notification.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nexgen_core import i18n, quota, quota_claude, quota_state, quota_tokscale, quota_watch
from nexgen_core.checks.quota_checks import check_quota

READ_AT = datetime(2026, 10, 23, 11, 21, 43, tzinfo=timezone.utc).timestamp()
NOW = READ_AT + 240.532  # four whole minutes after the reading


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: the notes and the line are asserted below."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


class RecordingMegaphone:
    """A megaphone that remembers the alerts it was asked to send."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_alert(self, title: str, message: str, action: str | None = None,
                   alert_key: str | None = None) -> bool:
        self.sent.append({"title": title, "message": message, "action": action, "key": alert_key})
        return True


def quota_file(home: Path, text: str) -> Path:
    """A `quota.yaml` at the path the report reads it from."""
    path = home / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def copilot_plan(monkeypatch, *, percent: float = 100.0) -> None:
    """The optional backend's answer for a Copilot plan, with no binary involved."""
    usage = quota_tokscale.TokscaleUsage(
        read_at=READ_AT,
        metrics=(quota_tokscale.Metric(
            provider="Copilot", provider_id="copilot", window="Premium",
            used_percent=percent, resets_at=None, read_at=READ_AT,
        ),),
    )

    def answer(home=None, *, config=None, now=None, max_age=None):
        return usage

    monkeypatch.setattr(quota_tokscale, "read_usage", answer)


def codex_plan(home: Path, *, percent: float = 59.0) -> None:
    """Codex's own reading, from the tail of a session written at `READ_AT`."""
    folder = home / ".codex" / "sessions" / "2026" / "10" / "23"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(READ_AT, timezone.utc).isoformat().replace("+00:00", "Z")
    (folder / "rollout-2026-10-23.jsonl").write_text(json.dumps({
        "timestamp": stamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": percent, "window_minutes": 720,
                            "resets_at": int(READ_AT) + 3600},
                "secondary": None,
                "credits": {"has_credits": False, "balance": None},
                "plan_type": "free",
            },
        },
    }) + "\n", encoding="utf-8")


# ----------------------------------------------------------------- what the key names


def test_the_plan_nobody_uses_is_the_one_named_copilot():
    """`ignore: [copilot]` works because that is the id tokscale's name folds to."""
    assert quota_tokscale.provider_id("Copilot") == "copilot"


# ------------------------------------------------------------------ the rows it hides


def test_a_provider_named_in_ignore_leaves_the_percentages(tmp_path, monkeypatch):
    copilot_plan(monkeypatch)
    quota_file(tmp_path, "ignore: [copilot]\n")

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    assert rows == []


def test_without_the_key_the_plan_is_printed_as_it_was(tmp_path, monkeypatch):
    copilot_plan(monkeypatch)

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    assert [row.provider for row in rows] == ["Copilot"]


def test_the_name_the_report_prints_is_enough_whatever_its_case(tmp_path, monkeypatch):
    copilot_plan(monkeypatch)
    quota_file(tmp_path, "ignore: [CoPiLoT]\n")

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    assert rows == []


def test_an_entry_may_be_the_id_a_chain_speaks(tmp_path, monkeypatch):
    """`openai` is the id Codex is reached by; the printed name is `Codex (free)`."""
    copilot_plan(monkeypatch)
    codex_plan(tmp_path)
    quota_file(tmp_path, "ignore: [openai]\n")

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    assert [row.provider for row in rows] == ["Copilot"]


# ------------------------------------------------------------------- every surface


def test_the_report_names_what_it_hides_in_one_line_at_the_bottom(tmp_path, monkeypatch):
    copilot_plan(monkeypatch)
    codex_plan(tmp_path)
    quota_file(tmp_path, "ignore: [copilot]\n")

    report = quota.build_report(home=tmp_path, now=NOW)

    assert "Copilot" not in "\n".join(report.lines)
    assert report.lines[-1] == "Ignored providers: copilot."


def test_a_plan_this_machine_never_reads_is_still_named(tmp_path):
    quota_file(tmp_path, "ignore: [copilot]\n")

    report = quota.build_report(home=tmp_path, now=NOW)

    assert report.lines[-1] == "Ignored providers: copilot."


def test_the_hourly_alert_is_silent_for_an_ignored_plan(tmp_path, monkeypatch):
    copilot_plan(monkeypatch)
    quota_file(tmp_path, "ignore: [copilot]\n")
    phone = RecordingMegaphone()

    answer = quota_watch.check_and_alert(home=tmp_path, megaphone=phone, now=NOW)

    assert answer["judged"] == []
    assert phone.sent == []


def test_the_failover_state_carries_no_ignored_plan(tmp_path, monkeypatch):
    copilot_plan(monkeypatch)
    quota_file(tmp_path, "ignore: [copilot]\n")

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    assert quota_state.payload(rows, now=NOW)["providers"] == {}


def test_the_doctor_line_forgets_an_ignored_plan(tmp_path, monkeypatch):
    copilot_plan(monkeypatch)
    quota_file(tmp_path, "ignore: [copilot]\n")

    outcome = check_quota(tmp_path, tmp_path / "KnowledgeVault")

    assert outcome.message == "No provider's percentage is readable on this machine."


def test_an_ignored_claude_plan_is_never_read_from_the_transcripts(tmp_path, monkeypatch):
    """A week of transcripts is hundreds of megabytes: a plan left out costs no read."""
    quota_file(tmp_path, "ignore: [anthropic]\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("an ignored plan's transcripts were read")

    monkeypatch.setattr(quota_claude, "read_usage", forbidden)

    report = quota.build_report(home=tmp_path, now=NOW)

    assert all(row.provider != "Claude Code" for row in report.rows)


def test_an_ignored_plan_gets_no_not_measured_placeholder(tmp_path):
    quota_file(tmp_path, "ignore: [antigravity]\n")

    report = quota.build_report(home=tmp_path, now=NOW)

    assert all(row.provider != "Antigravity" for row in report.rows)
    assert report.lines[-1] == "Ignored providers: antigravity."


# ------------------------------------------------------------------ a malformed value


def test_a_value_that_is_not_a_list_hides_nothing_and_says_so(tmp_path, monkeypatch):
    copilot_plan(monkeypatch)
    quota_file(tmp_path, "ignore: copilot\n")

    report = quota.build_report(home=tmp_path, now=NOW)

    assert "Copilot" in "\n".join(report.lines)
    assert sum("is not a list of providers" in note for note in report.notes) == 1
    assert not any(line.startswith("Ignored providers") for line in report.lines)


def test_an_entry_that_is_not_a_name_is_named_and_the_others_still_apply(tmp_path, monkeypatch):
    copilot_plan(monkeypatch)
    quota_file(tmp_path, "ignore:\n  - 3\n  - copilot\n")

    rows, notes = quota.percentage_rows(home=tmp_path, now=NOW)

    assert rows == []
    assert sum("position 1" in note for note in notes) == 1
