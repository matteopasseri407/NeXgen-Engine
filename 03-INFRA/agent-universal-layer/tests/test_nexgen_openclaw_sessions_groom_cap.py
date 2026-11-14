"""Tests for the session groomer compaction cap and queue reporting (L2).

nexgen: one-job reason="verify that groom compacts at most one session (largest or over-limit) and reports leftover sessions"
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import sessions_groom  # noqa: E402
from nexgen_core.openclaw.sessions_groom import HARD_TOKENS, IDLE_SECONDS  # noqa: E402

NOW = 200_000.0 * 1000.0


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


def run_groom(gateway, monkeypatch, home, dry_run=False, now=NOW):
    monkeypatch.setenv("NEXGEN_LANG", "en")
    monkeypatch.setattr(sessions_groom.subprocess, "run", gateway)
    return sessions_groom.groom(
        openclaw="openclaw", now_ms=now, dry_run=dry_run, emit=lambda _line: None, home=home
    )


def test_groom_compacts_at_most_one_and_reports_leftover(monkeypatch, tmp_path):
    s1 = idle_session(key="agent:main:s1", totalTokens=50_000)
    s2 = idle_session(key="agent:main:s2", totalTokens=200_000)
    s3 = idle_session(key="agent:main:s3", totalTokens=100_000)
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
    s1 = idle_session(key="agent:main:s1", totalTokens=50_000)
    s2 = idle_session(key="agent:main:s2", totalTokens=200_000)
    s3 = idle_session(key="agent:main:s3", totalTokens=100_000)
    gateway = FakeGateway([s1, s2, s3])

    monkeypatch.setenv("NEXGEN_OPENCLAW_MAX_COMPACTIONS", "2")
    report = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:s2", "agent:main:s3"]
    assert any("1" in line and ("left" in line or "lasciat" in line) for line in report.lines)
