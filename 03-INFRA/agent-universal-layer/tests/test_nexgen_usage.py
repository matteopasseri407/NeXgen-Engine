"""`nexgen usage`: what this machine spent, per provider, and never a number it did not read.

OpenClaw's `/usage cost` sums its own session logs, so an agent on a subscription
reads as $0 and the flat lane that was paid for is invisible. The engine's answer
is the readings it already owns, grouped by provider, and a lane it cannot read
says so instead of showing a zero. Every source here is a file in `tmp_path`.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n, usage  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.usage import Provider, Window  # noqa: E402 - sys.path shim for cloned checkout

#: One hour ago: the readers walk files by their last write, so a moment in the
#: future would exclude every file this test writes now.
NOW = time.time() - 3600.0


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def harness_archive(home: Path, rows: list[tuple[float, float, str]]) -> Path:
    """The harness's own store, with one row per session: when it moved, its cost, its model."""
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(
        "create table session_v2 (id text primary key, time_updated integer, cost real, model text)"
    )
    for index, (updated, cost, provider) in enumerate(rows):
        con.execute(
            "insert into session_v2 values (?, ?, ?, ?)",
            (f"ses_{index}", int(updated * 1000), cost, json.dumps({"providerID": provider})),
        )
    con.commit()
    con.close()
    return db


def by_name(reading: usage.Usage) -> dict[str, Provider]:
    return {provider.name: provider for provider in reading.providers}


def line_of(reading: usage.Usage, name: str) -> str:
    return next(line for line in reading.lines if line.startswith(f"{name}:"))


# ------------------------------------------------------------------ what it reads


def test_the_flat_lane_reports_the_spend_of_the_last_thirty_days(tmp_path):
    harness_archive(tmp_path, [
        (NOW - 3600, 0.25, "opencode-go"),
        (NOW - 3 * 86400, 1.75, "opencode-go"),
        (NOW - 40 * 86400, 9.0, "opencode-go"),  # outside the widest window
    ])

    reading = usage.build(home=tmp_path, now=NOW)

    windows = {window.label: window.used for window in by_name(reading)["OpenCode Go"].windows}
    assert windows == {"5h": 0.25, "7d": 2.0, "30d": 2.0}
    assert "2.00 $ in the last 30d" in line_of(reading, "OpenCode Go")


def test_the_two_paid_lanes_are_never_added_together(tmp_path):
    """`opencode-go` is a flat subscription and `opencode` is Zen: two invoices, two lines."""
    harness_archive(tmp_path, [(NOW - 3600, 1.0, "opencode-go"), (NOW - 3600, 4.0, "opencode")])

    reading = usage.build(home=tmp_path, now=NOW)

    assert by_name(reading)["OpenCode Go"].windows[-1].used == 1.0
    assert by_name(reading)["OpenCode Zen"].windows[-1].used == 4.0


def test_a_lane_with_no_session_says_so_instead_of_showing_a_zero(tmp_path):
    harness_archive(tmp_path, [])

    line = line_of(usage.build(home=tmp_path, now=NOW), "OpenCode Go")

    assert line == "OpenCode Go: no session in the last 30d"


def test_an_archive_that_cannot_be_read_is_named_not_guessed(tmp_path):
    """No store at all: the sentence says the reading did not happen."""
    line = line_of(usage.build(home=tmp_path, now=NOW), "OpenCode Go")

    assert line == "OpenCode Go: the harness archive could not be read"


def test_claude_reports_the_tokens_it_wrote_and_that_the_plan_limit_is_not_here(tmp_path):
    projects = tmp_path / ".claude" / "projects" / "one"
    projects.mkdir(parents=True)
    stamp = datetime.fromtimestamp(NOW - 60).astimezone().isoformat(timespec="seconds")
    (projects / "session.jsonl").write_text(
        json.dumps({
            "timestamp": stamp,
            "message": {"id": "msg_1", "usage": {"input_tokens": 100, "output_tokens": 50}},
        }) + "\n",
        encoding="utf-8",
    )

    line = line_of(usage.build(home=tmp_path, now=NOW), "Claude Code")

    assert "150 tokens in the last 5h" in line
    assert "the plan's limit is not readable on this machine" in line


def test_no_claude_transcript_is_a_sentence_not_a_zero(tmp_path):
    line = line_of(usage.build(home=tmp_path, now=NOW), "Claude Code")

    assert line.startswith("Claude Code: ")
    assert "tokens in the last" not in line


def test_codex_repeats_the_percentage_its_own_event_carries(tmp_path):
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "10" / "24"
    sessions.mkdir(parents=True)
    (sessions / "rollout-1.jsonl").write_text(
        json.dumps({"payload": {"rate_limits": {
            "primary": {"used_percent": 42.0, "window_minutes": 300, "resets_at": int(NOW) + 600},
            "secondary": {"used_percent": 71.0, "window_minutes": 10080},
        }}}) + "\n",
        encoding="utf-8",
    )

    line = line_of(usage.build(home=tmp_path, now=NOW), "Codex")

    assert "42% of the 5h window used" in line
    assert "71% of the 7d window used" in line


def test_antigravity_with_no_log_is_not_measured(tmp_path):
    assert line_of(usage.build(home=tmp_path, now=NOW), "Antigravity").startswith("Antigravity: ")


# -------------------------------------------------------------------- the shape


def test_the_json_carries_the_providers_and_the_lines_the_chat_shows(tmp_path):
    harness_archive(tmp_path, [(NOW - 3600, 2.5, "opencode-go")])

    payload = json.loads(json.dumps(usage.build(home=tmp_path, now=NOW).to_dict()))

    assert payload["providers"][0]["name"] == "OpenCode Go"
    assert payload["providers"][0]["windows"][-1] == {"window": "30d", "used": 2.5, "unit": "usd", "note": ""}
    assert payload["lines"] == usage.build(home=tmp_path, now=NOW).lines
    assert payload["read_at"].startswith("20")


def test_every_known_lane_answers_even_when_it_cannot_be_read(tmp_path):
    """A chat that asks must get one line per provider it could have read, never silence."""
    names = [line.split(":", 1)[0] for line in usage.build(home=tmp_path, now=NOW).lines]

    assert names == ["OpenCode Go", "OpenCode Zen", "Claude Code", "Codex", "Antigravity"]


def test_a_window_with_no_number_is_a_sentence_and_never_a_zero():
    assert usage.render_window(Window(label="5h", used=None, unit="usd")) == "not measured"
    assert usage.render_window(Window(label="5h", used=1.0)) == "not measured"


def test_a_provider_with_no_window_prints_its_reason():
    assert usage.render_provider(Provider(name="Codex", note="no reading")) == "Codex: no reading"


def test_the_quantity_is_written_once_by_the_engine():
    """The chat forwards these lines, so they carry their own unit and rounding."""
    assert usage.render_window(Window(label="30d", used=2.0, unit="usd")) == "2.00 $ in the last 30d"
    assert usage.render_window(Window(label="5h", used=1234, unit="tokens")) == "1,234 tokens in the last 5h"
    assert usage.render_window(Window(label="7d", used=71.25, unit="percent")) == "71.2% of the 7d window used"


# ------------------------------------------------------------------- the command


def test_the_usage_verb_prints_its_lines(tmp_path, monkeypatch, capsys):
    from nexgen_core.cli import tool_cmds

    monkeypatch.setattr(usage, "build", lambda: usage.Usage(
        read_at=NOW, providers=(Provider(name="OpenCode Go", windows=(Window("30d", 2.0, "usd"),)),),
    ))

    assert tool_cmds.cmd_usage(type("Args", (), {"json": False})()) == 0
    assert capsys.readouterr().out.strip() == "OpenCode Go: 2.00 $ in the last 30d"


def test_the_usage_verb_prints_json_when_asked(tmp_path, monkeypatch, capsys):
    from nexgen_core.cli import tool_cmds

    monkeypatch.setattr(usage, "build", lambda: usage.Usage(
        read_at=NOW, providers=(Provider(name="OpenCode Go", windows=(Window("30d", 2.0, "usd"),)),),
    ))

    assert tool_cmds.cmd_usage(type("Args", (), {"json": True})()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lines"] == ["OpenCode Go: 2.00 $ in the last 30d"]


def test_the_parser_exposes_usage_with_json():
    from nexgen_core.cli import build_parser

    args = build_parser().parse_args(["usage", "--json"])

    assert args.command == "usage"
    assert args.json is True
