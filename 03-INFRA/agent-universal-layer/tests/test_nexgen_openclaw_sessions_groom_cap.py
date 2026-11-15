"""Tests for the session groomer compaction cap and queue reporting (L2).

nexgen: one-job reason="verify that groom compacts at most one session (largest or over-limit) and reports leftover sessions"
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402
from nexgen_core.openclaw import sessions_groom  # noqa: E402
from nexgen_core.openclaw.sessions_groom import HARD_TOKENS, IDLE_SECONDS  # noqa: E402

NOW = 200_000.0 * 1000.0


@pytest.fixture(autouse=True)
def _english_lines():
    """Pin the source language: these tests assert the sentences a person reads."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def session(**overrides):
    base = {
        "key": "agent:main:main",
        "agentId": "main",
        "totalTokens": 40_000,
        "updatedAt": NOW,
        "lastInteractionAt": NOW,
        "status": "done",
    }
    base.update(overrides)
    return base


def idle_session(**overrides):
    fields = {"lastInteractionAt": NOW - (IDLE_SECONDS + 120) * 1000.0}
    fields.update(overrides)
    return session(**fields)


class FakeGateway:
    def __init__(self, sessions, reply=(0, '{"ok": true, "compacted": true}', ""), listing=None):
        self.sessions = sessions
        self.reply = reply
        self.listing = listing
        self.compactions: list[str] = []
        self.argvs: list[list[str]] = []
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if len(argv) > 2 and argv[2] == "list":
            if self.listing is not None:
                return subprocess.CompletedProcess(argv, *self.listing)
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sessions": self.sessions}), "")
        self.argvs.append(list(argv))
        self.compactions.append(argv[3])
        code, out, err = self.reply(argv[3]) if callable(self.reply) else self.reply
        return subprocess.CompletedProcess(argv, code, out, err)


def run_groom(gateway, monkeypatch, home, dry_run=False, now=NOW, phone=None):
    monkeypatch.setenv("NEXGEN_LANG", "en")
    monkeypatch.setattr(sessions_groom.subprocess, "run", gateway)
    return sessions_groom.groom(
        openclaw="openclaw", now_ms=now, dry_run=dry_run, emit=lambda _line: None, home=home, phone=phone
    )


class FakePhone:
    """The megaphone as the groom sees it: records the alerts it was asked to send."""

    def __init__(self):
        self.alerts: list[dict] = []

    def send_alert(self, **fields):
        self.alerts.append(fields)
        return True


def test_groom_compacts_at_most_one_and_reports_leftover(monkeypatch, tmp_path):
    s1 = idle_session(key="agent:main:s1", totalTokens=160_000)
    s2 = idle_session(key="agent:main:s2", totalTokens=200_000)
    s3 = idle_session(key="agent:main:s3", totalTokens=180_000)
    gateway = FakeGateway([s1, s2, s3])

    report = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:s2"]
    assert any("2" in line and ("left" in line or "lasciat" in line) for line in report.lines)


def test_groom_picks_session_over_hard_tokens_first(monkeypatch, tmp_path):
    s_idle_huge = idle_session(key="agent:main:idle_huge", totalTokens=500_000)
    s_hard = session(key="agent:main:hard", totalTokens=HARD_TOKENS + 10_000)
    gateway = FakeGateway([s_idle_huge, s_hard])

    report = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:hard"]
    assert any("1" in line and ("left" in line or "lasciat" in line) for line in report.lines)


def test_groom_none_eligible_makes_minimum_calls(monkeypatch, tmp_path):
    gateway = FakeGateway([])

    report = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == []
    assert report.lines == []
    assert len(gateway.calls) == 1
    assert gateway.calls[0][1:3] == ["sessions", "list"]


def test_groom_environment_variable_raises_cap(monkeypatch, tmp_path):
    s1 = idle_session(key="agent:main:s1", totalTokens=160_000)
    s2 = idle_session(key="agent:main:s2", totalTokens=200_000)
    s3 = idle_session(key="agent:main:s3", totalTokens=180_000)
    gateway = FakeGateway([s1, s2, s3])

    monkeypatch.setenv("NEXGEN_OPENCLAW_MAX_COMPACTIONS", "2")
    report = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:s2", "agent:main:s3"]
    assert any("1" in line and ("left" in line or "lasciat" in line) for line in report.lines)


def test_a_failing_chat_does_not_keep_the_next_one_waiting(monkeypatch, tmp_path):
    """The cap spends a slot on an outcome, never on an attempt: one refusal is not a queue."""
    big = idle_session(key="agent:main:big", totalTokens=500_000)
    small = idle_session(key="agent:main:small", totalTokens=200_000)

    def reply(key):
        if key == "agent:main:big":
            return 1, "", "Error: the gateway refused the command."
        return 0, '{"ok": true, "compacted": true}', ""

    gateway = FakeGateway([big, small], reply=reply)

    report = run_groom(gateway, monkeypatch, tmp_path, phone=FakePhone())

    assert gateway.compactions == ["agent:main:big", "agent:main:small"]
    assert report.failed == 1
    assert not any("left for later" in line for line in report.lines)


def test_a_chat_the_gateway_refused_is_left_alone_for_six_hours(monkeypatch, tmp_path):
    """The chat that spent the run waits, so the next run starts with the others."""
    big = idle_session(key="agent:main:big", totalTokens=500_000)
    small = idle_session(key="agent:main:small", totalTokens=200_000)

    def reply(key):
        if key == "agent:main:big":
            return 1, "", "Error: the gateway refused the command."
        return 0, '{"ok": true, "compacted": true}', ""

    gateway = FakeGateway([big, small], reply=reply)
    run_groom(gateway, monkeypatch, tmp_path, phone=FakePhone())

    gateway.compactions.clear()
    gateway.sessions = [idle_session(key="agent:main:big", totalTokens=500_000),
                        idle_session(key="agent:main:small", totalTokens=200_000)]
    run_groom(gateway, monkeypatch, tmp_path, now=NOW + 60 * 60 * 1000, phone=FakePhone())
    assert gateway.compactions == []

    run_groom(gateway, monkeypatch, tmp_path, now=NOW + 6 * 60 * 60 * 1000, phone=FakePhone())
    assert gateway.compactions == ["agent:main:big"]


def test_a_gateway_that_refuses_everything_costs_one_attempt_over_the_cap(monkeypatch, tmp_path):
    """The queue is walked past a failure, not through it: the run still ends."""
    chats = [idle_session(key=f"agent:main:s{i}", totalTokens=200_000 + 10_000 * i) for i in range(4)]
    gateway = FakeGateway(chats, reply=(1, "", "Error: the gateway refused the command."))

    report = run_groom(gateway, monkeypatch, tmp_path, phone=FakePhone())

    assert gateway.compactions == ["agent:main:s3", "agent:main:s2"]
    assert report.failed == 2


def test_zero_switches_the_groom_off(monkeypatch, tmp_path):
    """NEXGEN_OPENCLAW_MAX_COMPACTIONS=0 is how a person stops the hourly bill."""
    gateway = FakeGateway([idle_session(totalTokens=200_000)])

    monkeypatch.setenv("NEXGEN_OPENCLAW_MAX_COMPACTIONS", "0")
    report = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == []
    assert any("switched off" in line for line in report.lines)


def test_the_cap_reads_a_count_and_falls_back_on_nonsense():
    def cap(raw: str) -> int:
        return sessions_groom.get_max_compactions({"NEXGEN_OPENCLAW_MAX_COMPACTIONS": raw})

    assert cap("0") == 0
    assert cap("3") == 3
    assert cap(" 3 ") == 3
    assert cap("") == 1
    assert cap("-1") == 1
    assert cap("abc") == 1


def test_a_provider_that_spent_its_quota_leaves_its_other_chats_for_later(monkeypatch, tmp_path):
    """Asking a second chat on a spent provider only earns another rejection."""
    spent = '{"ok": false, "compacted": false, "reason": "429 Go usage limit exceeded"}'
    first = idle_session(key="agent:one:main", agentId="one", modelProvider="opencode-go", totalTokens=200_000)
    second = idle_session(key="agent:two:main", agentId="two", modelProvider="opencode-go", totalTokens=160_000)
    gateway = FakeGateway([first, second], reply=(0, spent, ""))

    report = run_groom(gateway, monkeypatch, tmp_path, phone=FakePhone())

    assert gateway.compactions == ["agent:one:main"]
    assert "1 eligible session left for later" in report.lines


def test_a_dry_run_previews_the_whole_queue(monkeypatch, tmp_path):
    """A preview of five queued chats is five chats: nothing was compacted or deferred."""
    chats = [idle_session(key=f"agent:main:s{i}", totalTokens=200_000 + 10_000 * i) for i in range(3)]
    gateway = FakeGateway(chats)

    report = run_groom(gateway, monkeypatch, tmp_path, dry_run=True)

    assert gateway.compactions == []
    shown = [line for line in report.lines if line.endswith("-> dry-run")]
    assert [line.split(": ")[0] for line in shown] == ["agent:main:s2", "agent:main:s1", "agent:main:s0"]
    assert "A real run compacts at most 1 of these, largest first." in report.lines


def test_a_stalled_queue_raises_one_alert(monkeypatch, tmp_path):
    """The job runs with --no-deliver: without an alert, a stalled queue is silent."""
    chats = [idle_session(key=f"agent:main:s{i}", totalTokens=200_000 + 10_000 * i) for i in range(3)]
    gateway = FakeGateway(chats, reply=(1, "", "Error: the gateway refused the command."))
    phone = FakePhone()

    run_groom(gateway, monkeypatch, tmp_path, phone=phone)

    assert [alert["alert_key"] for alert in phone.alerts] == ["openclaw:groom:stalled"]
    assert phone.alerts[0]["message"] == "The last run compacted none of the chats waiting for it. Waiting now: 1."


def test_a_run_that_compacted_something_raises_no_alert(monkeypatch, tmp_path):
    phone = FakePhone()

    report = run_groom(FakeGateway([idle_session()]), monkeypatch, tmp_path, phone=phone)

    assert report.failed == 0
    assert phone.alerts == []
