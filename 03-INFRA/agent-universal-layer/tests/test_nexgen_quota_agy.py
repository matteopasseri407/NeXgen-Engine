"""Antigravity's plan is read from its own CLI (`agy -p /usage`), not from a dead IDE extension log.

The engine reported Antigravity with a 76-day-old reading: the reader parsed a log
the IDE quota extension had stopped writing, while the plan Kurisu runs on is the
CLI's. The table below is what agy printed on the VPS on 2026-11-15.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nexgen_core import quota, quota_agy

TABLE = (
    "Gemini Models\tWeekly Limit Remaining\t49%\t2026-11-21T09:14:46Z\n"
    "Gemini Models\tFive Hour Limit Remaining\t96%\t2026-11-16T00:00:23Z\n"
    "Claude and GPT models\tWeekly Limit Remaining\t100%\t2026-11-22T11:48:28Z\n"
    "Claude and GPT models\tFive Hour Limit Remaining\t100%\t2026-11-16T00:39:22Z\n"
)
NOW = 1_794_770_000.0


class FakeAgy:
    def __init__(self, stdout: str = TABLE, code: int = 0):
        self.stdout, self.code, self.calls = stdout, code, []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.code, self.stdout, "")


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("NEXGEN_QUOTA_AGY", raising=False)  # these tests drive a fake client
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def _read(home: Path, agy: FakeAgy, now: float = NOW, which=lambda name: "/usr/bin/agy"):
    return quota_agy.read_windows(home=home, now=now, run=agy, which=which)


def test_the_client_table_becomes_one_window_per_row(home):
    agy = FakeAgy()

    windows = _read(home, agy)

    assert [(w.group, w.window, w.remaining_percent) for w in windows] == [
        ("Gemini Models", "Weekly", 49.0),
        ("Gemini Models", "5h", 96.0),
        ("Claude and GPT models", "Weekly", 100.0),
        ("Claude and GPT models", "5h", 100.0),
    ]
    assert agy.calls[0][-4:] == ["-p", "/usage", "--output-format", "text"]


def test_the_rows_say_what_is_used_and_only_gemini_carries_the_provider_id(home):
    rows = quota_agy.to_rows(_read(home, FakeAgy()))

    weekly = rows[0]
    assert weekly.provider == "Antigravity"
    assert weekly.percent == 51.0
    assert weekly.provider_id == "google-antigravity-cli"
    assert rows[2].provider_id == ""


def test_one_answer_serves_every_caller_for_ten_minutes(home):
    agy = FakeAgy()
    _read(home, agy)

    _read(home, agy, now=NOW + 300)
    assert len(agy.calls) == 1

    _read(home, agy, now=NOW + quota_agy.CACHE_TTL_SECONDS + 1)
    assert len(agy.calls) == 2


def test_no_client_and_a_failing_client_are_no_reading(home):
    assert _read(home, FakeAgy(), which=lambda name: None) == []
    assert _read(home, FakeAgy(code=1)) == []
    assert _read(home, FakeAgy(stdout="Please sign in to Antigravity first.\n")) == []


def test_the_report_prefers_the_client_over_the_extension_log(home, monkeypatch):
    monkeypatch.setattr(quota_agy, "read_windows", lambda **_: quota_agy.parse(TABLE, NOW))

    rows = quota._antigravity_rows(home, NOW, [])

    assert {row.window for row in rows} >= {"Weekly (Gemini Models)", "5h (Gemini Models)"}
    assert all(row.percent is not None for row in rows)


def test_the_switch_stops_the_client_from_being_asked(home, monkeypatch):
    monkeypatch.setenv("NEXGEN_QUOTA_AGY", "off")
    agy = FakeAgy()

    assert _read(home, agy) == []
    assert agy.calls == []
