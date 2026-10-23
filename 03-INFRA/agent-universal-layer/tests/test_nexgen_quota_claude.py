"""Claude Code's own transcripts, read for tokens and for nothing else.

The fixtures are transcripts built here with the shape a live one has: one JSON
object per line, `message.usage` carrying the counters, `message.id` naming the
reply. The two behaviours under test are the ones that were wrong on a real
machine before they were written down: the same reply written many times, and a
week of files that must not all be opened.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nexgen_core import i18n
from nexgen_core.quota_claude import read_usage

#: A fixed instant, so a window boundary is arithmetic and not luck.
NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: these tests assert note contents, and the
    machine locale would otherwise turn them Italian."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def message(*, ago: float, message_id: str = "msg_1", sent: int = 1000, received: int = 0,
            cached: int = 0, when: str | None = None) -> dict:
    """One assistant message, as Claude Code writes it."""
    if when is None:
        stamp = datetime.fromtimestamp(NOW - ago, timezone.utc).isoformat().replace("+00:00", "Z")
    else:
        stamp = when
    return {
        "type": "assistant",
        "timestamp": stamp,
        "message": {
            "id": message_id,
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": sent,
                "output_tokens": received,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cached,
            },
        },
    }


def transcript(claude: Path, rows: list, *, project: str = "progetto", name: str = "session.jsonl",
               age: float = 0.0) -> Path:
    """A transcript at the path Claude Code uses, last written `age` seconds ago."""
    folder = claude / "projects" / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    return path


def test_tokens_are_summed_per_window_from_the_usage_counters(tmp_path):
    claude = tmp_path / "claude"
    transcript(claude, [message(ago=600, message_id="msg_a", sent=1000, received=200, cached=500)])

    usage = read_usage(claude, now=NOW)

    assert usage.known
    assert usage.windows["5h"] == {"tokens": 1700, "messages": 1}
    assert usage.windows["7d"] == {"tokens": 1700, "messages": 1}


def test_a_window_stops_at_its_own_edge(tmp_path):
    claude = tmp_path / "claude"
    transcript(claude, [
        message(ago=3600, message_id="msg_in_5h", sent=100),
        message(ago=6 * 3600, message_id="msg_in_7d", sent=1000),
        message(ago=8 * 86400, message_id="msg_too_old", sent=100000),
    ])

    usage = read_usage(claude, now=NOW)

    assert usage.windows["5h"]["tokens"] == 100
    assert usage.windows["7d"]["tokens"] == 1100


def test_the_same_reply_written_twenty_times_is_counted_once(tmp_path):
    """Claude Code appends the same assistant message again on every resume."""
    claude = tmp_path / "claude"
    transcript(claude, [message(ago=60, message_id="msg_same", sent=1000, received=200)] * 20)

    usage = read_usage(claude, now=NOW)

    assert usage.windows["5h"] == {"tokens": 1200, "messages": 1}


def test_two_replies_of_the_same_session_are_both_counted(tmp_path):
    claude = tmp_path / "claude"
    transcript(claude, [
        message(ago=120, message_id="msg_1", sent=100),
        message(ago=60, message_id="msg_2", sent=200),
    ])

    usage = read_usage(claude, now=NOW)

    assert usage.windows["5h"] == {"tokens": 300, "messages": 2}


def test_a_session_split_over_two_files_is_counted_across_them(tmp_path):
    claude = tmp_path / "claude"
    transcript(claude, [message(ago=900, message_id="msg_1", sent=100)], project="a")
    transcript(claude, [message(ago=600, message_id="msg_2", sent=200)], project="b")

    usage = read_usage(claude, now=NOW)

    assert usage.windows["5h"] == {"tokens": 300, "messages": 2}


def test_a_transcript_older_than_the_wide_window_is_never_opened(tmp_path):
    claude = tmp_path / "claude"
    # A file whose last write is well outside the window cannot hold a message
    # inside it: it is left alone even though its rows claim otherwise.
    stale = transcript(claude, [message(ago=60, message_id="msg_stale", sent=9_000_000)], age=30 * 86400)

    usage = read_usage(claude, now=NOW)

    assert not usage.known
    assert stale.is_file()  # never deleted, only not read


def test_a_message_with_no_moment_is_not_placed_in_any_window(tmp_path):
    claude = tmp_path / "claude"
    transcript(claude, [message(ago=60, when="", sent=5000)])

    usage = read_usage(claude, now=NOW)

    assert not usage.known
    assert usage.windows["5h"]["tokens"] == 0


def test_a_machine_with_no_claude_home_says_so_instead_of_zero(tmp_path):
    usage = read_usage(tmp_path / "nowhere", now=NOW)

    assert not usage.known
    assert "does not exist" in usage.note
    assert str(tmp_path / "nowhere" / "projects") in usage.note


def test_a_claude_home_with_only_old_transcripts_says_how_long_it_looked(tmp_path):
    claude = tmp_path / "claude"
    (claude / "projects").mkdir(parents=True)

    usage = read_usage(claude, now=NOW)

    assert not usage.known
    assert "7 days" in usage.note


def test_a_line_that_is_not_a_message_is_skipped(tmp_path):
    claude = tmp_path / "claude"
    path = transcript(claude, [message(ago=60, message_id="msg_ok", sent=700)])
    path.write_text(
        path.read_text(encoding="utf-8")
        + '{"type":"user","message":{"role":"user","content":"hi"}}\n'
        + '{"type":"assistant","message":{"id":"msg_broken"}}\n'
        + 'not json at all\n',
        encoding="utf-8",
    )

    usage = read_usage(claude, now=NOW)

    assert usage.windows["5h"] == {"tokens": 700, "messages": 1}


def test_a_timestamp_with_an_offset_is_read_as_utc(tmp_path):
    claude = tmp_path / "claude"
    moment = datetime.fromtimestamp(NOW - 120, timezone.utc)
    transcript(claude, [message(ago=120, when=moment.astimezone(timezone(timedelta(hours=2))).isoformat())])

    usage = read_usage(claude, now=NOW)

    assert usage.windows["5h"]["tokens"] == 1000
