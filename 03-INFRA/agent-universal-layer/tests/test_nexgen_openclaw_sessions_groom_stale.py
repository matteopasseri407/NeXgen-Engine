"""Tests for fresh and stale token reporting in session groom lines (D2).

nexgen: one-job reason="verify that groom line reports 'last known N tokens, counter stale' when totalTokensFresh is false"
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.sessions_groom import Decision, _line, plan_groom  # noqa: E402

NOW = 1_800_000_000_000


def test_line_formatting_fresh_tokens() -> None:
    decision = Decision(
        key="agent:main:main",
        agent_id="main",
        tokens=50000,
        idle_seconds=3600.0,
        reason="idle",
        runtime="",
        fingerprint={},
        tokens_fresh=True,
    )
    line = _line(decision, "compacted")
    assert line == "agent:main:main: 50000 tokens, idle 60m (idle) -> compacted"
    assert "counter stale" not in line


def test_line_formatting_stale_tokens() -> None:
    decision = Decision(
        key="agent:main:main",
        agent_id="main",
        tokens=715455,
        idle_seconds=3600.0,
        reason="idle",
        runtime="",
        fingerprint={},
        tokens_fresh=False,
    )
    line = _line(decision, "compacted")
    assert line == "agent:main:main: last known 715455 tokens, counter stale, idle 60m (idle) -> compacted"


def test_plan_groom_populates_tokens_fresh() -> None:
    fresh_session = {
        "key": "agent:main:main",
        "agentId": "main",
        "totalTokens": 40_000,
        "totalTokensFresh": True,
        "lastInteractionAt": NOW - 7200 * 1000,
        "status": "done",
    }
    stale_session = {
        "key": "agent:capo:main",
        "agentId": "capo",
        "totalTokens": 715_455,
        "totalTokensFresh": False,
        "lastInteractionAt": NOW - 7200 * 1000,
        "status": "done",
    }
    decisions = plan_groom([fresh_session, stale_session], NOW)
    assert len(decisions) == 2
    d_fresh = next(d for d in decisions if d.key == "agent:main:main")
    d_stale = next(d for d in decisions if d.key == "agent:capo:main")
    assert d_fresh.tokens_fresh is True
    assert d_stale.tokens_fresh is False
    assert "last known 715455 tokens, counter stale" in _line(d_stale, "ok")
    assert "40000 tokens, idle" in _line(d_fresh, "ok")
