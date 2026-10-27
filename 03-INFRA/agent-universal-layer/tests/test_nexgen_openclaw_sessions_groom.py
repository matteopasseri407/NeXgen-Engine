"""Tests for the session groomer policy: what gets compacted, and what never does."""
from __future__ import annotations

from nexgen_core.openclaw.sessions_groom import HARD_TOKENS, IDLE_SECONDS, plan_groom

NOW = 1_800_000_000_000


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


def test_an_idle_agent_chat_is_compacted():
    entry = session(lastInteractionAt=NOW - (IDLE_SECONDS + 60) * 1000)

    decisions = plan_groom([entry], NOW)

    assert [d.key for d in decisions] == ["agent:main:main"]
    assert decisions[0].reason == "idle"


def test_a_chat_in_use_stays_alone_until_it_grows_too_much():
    assert plan_groom([session()], NOW) == []
    huge = session(totalTokens=HARD_TOKENS + 1)
    assert plan_groom([huge], NOW)[0].reason == "over the limit"


def test_a_session_with_no_tokens_is_not_touched():
    idle = session(totalTokens=0, lastInteractionAt=NOW - 10 * IDLE_SECONDS * 1000)

    assert plan_groom([idle], NOW) == []


def test_a_running_session_is_left_to_finish():
    running = session(status="running", totalTokens=HARD_TOKENS + 1)

    assert plan_groom([running], NOW) == []


def test_subagent_and_automation_sessions_are_not_compacted_here():
    entries = [
        session(key="agent:main:subagent:abc"),
        session(key="agent:main:cron:job:run:1"),
        session(key="agent:main:acp:x"),
    ]

    assert plan_groom(entries, NOW) == []


def test_the_main_chat_is_never_skipped_by_key():
    forked = session(key="agent:main:dashboard:abc", lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000)

    decisions = plan_groom([forked], NOW)

    assert decisions and decisions[0].key == "agent:main:dashboard:abc"
