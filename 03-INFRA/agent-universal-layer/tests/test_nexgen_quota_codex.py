"""What Codex says about its own limit, read from a session file built here.

Every fixture lives under `tmp_path` and carries the shape a live session had on
2026-10-24: one JSON object per line, and the token-count events naming
`payload.rate_limits`. Nothing in this file looks at the machine running it.
"""
from __future__ import annotations

import json
import os
import time

import pytest
from datetime import datetime, timezone
from pathlib import Path

from nexgen_core import i18n
from nexgen_core.quota_codex import CodexUsage, read_usage

#: The moment the reference line was written, and its epoch.
WHEN = "2026-10-23T11:21:43.532Z"
WHEN_EPOCH = datetime(2026, 10, 23, 11, 21, 43, 532000, tzinfo=timezone.utc).timestamp()

#: The reset instants the fixtures carry: derived from that moment, an hour
#: and half an hour after the line was written, never a value read from a
#: session file on the machine that runs the test.
RESETS_AT = int(WHEN_EPOCH) + 3600
RESETS_SOON = int(WHEN_EPOCH) + 1800


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: these tests assert note contents, and the
    machine locale would otherwise turn them Italian."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def limit_line(*, used: float | None = 59.0, window: int | None = 43200,
               resets: int | None = RESETS_AT, secondary: dict | None = None,
               plan: str = "free", when: str = WHEN) -> dict:
    """One `token_count` event as Codex writes it, with the windows asked for."""
    primary = None if used is None else {
        "used_percent": used, "window_minutes": window, "resets_at": resets,
    }
    return {
        "timestamp": when,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": primary,
                "secondary": secondary,
                "credits": {"has_credits": False, "balance": None},
                "plan_type": plan,
            },
        },
    }


def rollout(codex: Path, rows: list[dict], *, name: str = "rollout-2026-10-24T00-00-00.jsonl",
            age: float = 0.0) -> Path:
    """A session file at the path Codex uses, older by `age` seconds when asked."""
    folder = codex / "sessions" / "2026" / "10" / "24"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    return path


def test_the_newest_reading_is_the_one_reported(tmp_path):
    codex = tmp_path / "codex"
    rollout(codex, [limit_line(used=12.0)], name="rollout-old.jsonl", age=3600)
    rollout(codex, [limit_line(used=59.0, plan="free")], name="rollout-new.jsonl")

    usage = read_usage(codex)

    assert usage.known
    assert usage.primary is not None and usage.primary.used_percent == 59.0
    assert usage.primary.window_minutes == 43200
    assert usage.primary.resets_at == RESETS_AT
    assert usage.plan == "free"
    assert usage.read_at is not None and abs(usage.read_at - WHEN_EPOCH) < 1


def test_a_newer_session_that_never_hit_a_limit_falls_back_to_the_older_one(tmp_path):
    codex = tmp_path / "codex"
    rollout(codex, [limit_line(used=12.0)], name="rollout-old.jsonl", age=3600)
    rollout(codex, [{"timestamp": WHEN, "type": "event_msg", "payload": {"type": "token_count"}}],
            name="rollout-new.jsonl")

    usage = read_usage(codex)

    assert usage.primary is not None and usage.primary.used_percent == 12.0


def test_both_windows_are_read_when_codex_reports_two(tmp_path):
    codex = tmp_path / "codex"
    rollout(codex, [limit_line(
        used=59.0,
        secondary={"used_percent": 12.5, "window_minutes": 300, "resets_at": RESETS_SOON},
    )])

    usage = read_usage(codex)

    assert usage.primary is not None and usage.primary.used_percent == 59.0
    assert usage.secondary is not None and usage.secondary.window_minutes == 300
    assert usage.secondary.used_percent == 12.5


def test_a_line_whose_two_windows_are_null_is_not_a_reading(tmp_path):
    codex = tmp_path / "codex"
    rollout(codex, [limit_line(used=12.0)], name="rollout-old.jsonl", age=3600)
    rollout(codex, [limit_line(used=None, window=None, resets=None)], name="rollout-new.jsonl")

    usage = read_usage(codex)

    assert usage.primary is not None and usage.primary.used_percent == 12.0


def test_a_corrupt_line_is_skipped_and_the_one_before_it_answers(tmp_path):
    codex = tmp_path / "codex"
    path = rollout(codex, [limit_line(used=42.0)])
    path.write_text(path.read_text(encoding="utf-8") + '{"truncated": tru\n', encoding="utf-8")

    usage = read_usage(codex)

    assert usage.primary is not None and usage.primary.used_percent == 42.0


def test_a_machine_with_no_codex_home_says_so_instead_of_zero(tmp_path):
    usage = read_usage(tmp_path / "nowhere")

    assert isinstance(usage, CodexUsage)
    assert not usage.known
    assert "does not exist" in usage.note
    assert str(tmp_path / "nowhere" / "sessions") in usage.note


def test_a_codex_home_with_no_session_says_so(tmp_path):
    codex = tmp_path / "codex"
    (codex / "sessions").mkdir(parents=True)

    usage = read_usage(codex)

    assert not usage.known
    assert "holds no rollout" in usage.note


def test_sessions_that_carry_no_limit_say_how_many_were_tried(tmp_path):
    codex = tmp_path / "codex"
    for index in range(3):
        rollout(codex, [{"timestamp": WHEN, "type": "event_msg", "payload": {"type": "token_count"}}],
                name=f"rollout-{index}.jsonl")

    usage = read_usage(codex)

    assert not usage.known
    assert "sessions examined (3)" in usage.note


def test_the_reading_is_looked_for_at_the_end_of_a_long_session(tmp_path):
    """A session is hundreds of megabytes; its newest reading is in the tail."""
    codex = tmp_path / "codex"
    filler = [{"timestamp": WHEN, "type": "response_item", "payload": {"text": "x" * 4096}}] * 200
    rollout(codex, [*filler, limit_line(used=7.5)])

    usage = read_usage(codex)

    assert usage.primary is not None and usage.primary.used_percent == 7.5


def test_a_reading_further_back_than_the_tail_budget_is_not_hunted_for(tmp_path):
    """The budget is deliberate: five sessions of history must not cost seconds."""
    from nexgen_core import quota_codex

    codex = tmp_path / "codex"
    filler = [{"timestamp": WHEN, "type": "response_item", "payload": {"text": "x" * 65536}}] * 160
    rollout(codex, [limit_line(used=7.5), *filler])

    assert quota_codex.TAIL_BUDGET_BYTES < 160 * 65536
    assert not read_usage(codex).known


def test_a_reading_without_its_own_moment_falls_back_to_the_file_mtime(tmp_path):
    codex = tmp_path / "codex"
    path = rollout(codex, [limit_line(used=21.0, when="")])
    os.utime(path, (1_800_000_000.0, 1_800_000_000.0))

    usage = read_usage(codex)

    assert usage.primary is not None and usage.primary.used_percent == 21.0
    assert usage.read_at == 1_800_000_000.0
