"""The quota report: one row per window, and an honest unknown where nothing is readable.

Every fixture is built under `tmp_path`: a Codex session with a rate-limit line,
a harness archive with three sessions, a `quota.yaml` with declared ceilings.
The report is asserted from what it prints and from what its JSON carries, so a
change in either surface has to be deliberate.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nexgen_core import i18n, quota

WHEN = "2026-10-23T11:21:43.532Z"
READ_AT = datetime(2026, 10, 23, 11, 21, 43, tzinfo=timezone.utc).timestamp()
NOW = READ_AT + 240.532  # the line's own milliseconds included: four whole minutes

#: The reset instant the fixture carries: an hour after the reading, computed
#: from it rather than copied from a session file on this machine.
RESETS_AT = int(READ_AT) + 3600


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: these tests assert note contents, and the
    machine locale would otherwise turn them Italian."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def run_quota(sandbox, *args: str):
    """The verb as the CLI runs it on the sandbox engine, with the sandbox env."""
    import subprocess
    import sys

    sandbox.assert_is_sandbox()
    return subprocess.run(
        [sys.executable, str(sandbox.scripts_dir / "nexgen_core" / "cli" / "__init__.py"), "quota", *args],
        env=sandbox.env(NEXGEN_LANG="en"), capture_output=True, text=True, timeout=120,
    )


def codex_reading(home: Path, *, used: float = 59.0, window: int = 43200,
                  resets: int = RESETS_AT, name: str = "rollout-2026-10-23.jsonl") -> Path:
    """A Codex session carrying one rate-limit line, where Codex writes it."""
    folder = home / ".codex" / "sessions" / "2026" / "10" / "23"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps({
        "timestamp": WHEN,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": used, "window_minutes": window, "resets_at": resets},
                "secondary": None,
                "credits": {"has_credits": False, "balance": None},
                "plan_type": "free",
            },
        },
    }) + "\n", encoding="utf-8")
    return path


def archive(home: Path, rows: list[tuple[str, str, float, float]]) -> Path:
    """The harness store: `(session, provider, cost, hours ago)` in `session_v2`."""
    path = home / ".local" / "share" / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "create table session_v2 (id text primary key, parent_id text, cost real, "
        "time_updated integer, model text)"
    )
    con.executemany(
        "insert into session_v2 values (?, null, ?, ?, ?)",
        [
            (session, cost, int((NOW - hours * 3600) * 1000), json.dumps({"providerID": provider}))
            for session, provider, cost, hours in rows
        ],
    )
    con.commit()
    con.close()
    return path


def write_limits(home: Path, text: str) -> Path:
    """A `quota.yaml` at the path the report reads it from."""
    path = home / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def rows_for(report: quota.QuotaReport, provider: str) -> list[quota.QuotaRow]:
    return [row for row in report.rows if row.provider == provider]


# ------------------------------------------------------------------ the report


def test_a_machine_with_nothing_readable_still_answers(tmp_path):
    report = quota.build_report(home=tmp_path, now=NOW)

    assert [row.provider for row in report.rows] == ["Antigravity"]
    assert report.rows[0].state == "unknown"
    text = "\n".join(report.lines)
    assert "Antigravity: not measured" in text
    assert "Codex sessions" in text or "session" in text.lower()
    assert "harness archive" in text


def test_codex_contributes_its_own_percentage_reset_and_age(tmp_path):
    codex_reading(tmp_path)

    report = quota.build_report(home=tmp_path, now=NOW)

    row = rows_for(report, "Codex (free)")[0]
    assert row.window == "30d"
    assert row.percent == 59.0
    assert abs(row.read_at - READ_AT) < 1
    line = report.lines[0]
    assert line.startswith("Codex (free): 59% of the 30d window used")
    assert "read 4m ago" in line
    assert "resets in" in line


def test_a_reading_older_than_its_window_loses_the_percentage(tmp_path):
    """A 5h window read six hours ago has reset: the number describes nothing."""
    codex_reading(tmp_path, window=300)

    report = quota.build_report(home=tmp_path, now=READ_AT + 6 * 3600)

    row = rows_for(report, "Codex (free)")[0]
    assert row.state == "stale"
    assert row.percent is None
    assert "older than the window itself" in report.lines[0]


def test_a_reading_inside_its_window_keeps_it(tmp_path):
    codex_reading(tmp_path, window=300)

    report = quota.build_report(home=tmp_path, now=READ_AT + 4 * 3600)

    assert rows_for(report, "Codex (free)")[0].percent == 59.0


def test_spend_is_split_by_provider_and_window(tmp_path):
    archive(tmp_path, [
        ("s1", "opencode-go", 1.0, 1.0),
        ("s2", "opencode-go", 4.0, 72.0),
        ("s3", "opencode", 0.0, 2.0),
    ])

    report = quota.build_report(home=tmp_path, now=NOW)

    go = {row.window: row for row in rows_for(report, "OpenCode Go")}
    assert go["5h"].used == 1.0
    assert go["7d"].used == 5.0
    assert go["30d"].used == 5.0
    assert all(row.percent is None for row in go.values())


def test_a_free_lane_is_shown_as_zero_and_not_hidden(tmp_path):
    archive(tmp_path, [("s1", "opencode", 0.0, 2.0)])

    report = quota.build_report(home=tmp_path, now=NOW)

    rows = rows_for(report, "OpenCode Zen")
    assert rows and all(row.used == 0.0 for row in rows)
    assert any("0.00 $ spent in the last 5h" in line for line in report.lines)


def test_a_declared_ceiling_turns_spend_into_a_percentage(tmp_path):
    archive(tmp_path, [("s1", "opencode-go", 1.0, 1.0), ("s2", "opencode-go", 4.0, 72.0)])
    write_limits(tmp_path, "limits:\n  opencode-go:\n    5h: 4.0\n    7d: 5.0\n")

    report = quota.build_report(home=tmp_path, now=NOW)

    go = {row.window: row for row in rows_for(report, "OpenCode Go")}
    assert go["5h"].percent == 25.0
    assert go["5h"].state == "ok"
    assert go["7d"].percent == 100.0
    assert go["7d"].state == "warn"
    assert any("1.00 $ of 4.00 $" in line for line in report.lines)


def test_a_ceiling_for_another_provider_is_not_borrowed(tmp_path):
    archive(tmp_path, [("s1", "opencode", 3.0, 1.0)])
    write_limits(tmp_path, "limits:\n  opencode-go:\n    5h: 4.0\n")

    report = quota.build_report(home=tmp_path, now=NOW)

    assert all(row.percent is None for row in rows_for(report, "OpenCode Zen"))


def test_an_unreadable_limits_file_cost_the_percentages_and_nothing_else(tmp_path):
    archive(tmp_path, [("s1", "opencode-go", 1.0, 1.0)])
    write_limits(tmp_path, "limits: [not: a: map\n")

    report = quota.build_report(home=tmp_path, now=NOW)

    assert rows_for(report, "OpenCode Go")[0].percent is None
    assert any("cannot be read" in line for line in report.notes)


def test_a_ceiling_that_is_not_a_positive_number_is_ignored_out_loud(tmp_path):
    archive(tmp_path, [("s1", "opencode-go", 1.0, 1.0)])
    write_limits(tmp_path, "limits:\n  opencode-go:\n    5h: 0\n    7d: niente\n")

    report = quota.build_report(home=tmp_path, now=NOW)

    assert rows_for(report, "OpenCode Go")[0].percent is None
    assert sum("is not a positive number" in line for line in report.notes) == 2


def test_claude_code_shows_tokens_and_says_its_limit_is_not_readable(tmp_path):
    folder = tmp_path / ".claude" / "projects" / "progetto"
    folder.mkdir(parents=True)
    first = NOW - 600
    (folder / "s.jsonl").write_text(json.dumps({
        "type": "assistant",
        "timestamp": datetime.fromtimestamp(first, timezone.utc).isoformat(),
        "message": {
            "id": "msg_1",
            "usage": {"input_tokens": 1000, "output_tokens": 234, "cache_read_input_tokens": 0},
        },
    }) + "\n", encoding="utf-8")

    report = quota.build_report(home=tmp_path, now=NOW)

    row = rows_for(report, "Claude Code")[0]
    assert row.window == "5h"
    assert row.used == 1234
    assert row.percent is None
    assert any(
        "1,234 tokens in the last 5h" in line and "the limit is not readable on this machine" in line
        for line in report.lines
    )


# --------------------------------------------------------------------- the json


def test_the_json_carries_the_lines_the_plugin_prints(tmp_path):
    codex_reading(tmp_path)
    archive(tmp_path, [("s1", "opencode-go", 1.0, 1.0)])

    data = quota.build_report(home=tmp_path, now=NOW).to_dict()

    report = quota.build_report(home=tmp_path, now=NOW)
    assert data["lines"][:len(data["rows"])] == [
        quota.render_row(row, NOW) for row in report.rows
    ]
    assert {"read_at", "rows", "lines", "notes"} <= set(data)
    codex_row = next(row for row in data["rows"] if row["provider"] == "Codex (free)")
    assert codex_row["percent"] == 59.0
    assert codex_row["age_seconds"] == 240
    assert codex_row["unit"] == "percent"
    assert codex_row["resets_at"].startswith("2026-")


def test_the_json_and_the_human_output_come_from_the_same_lines(tmp_path, capsys, monkeypatch):
    """The plugin prints `lines`; a person reads the same lines. One renderer."""
    codex_reading(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert quota.main() == 0
    human = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert quota.main(as_json=True) == 0
    data = json.loads(capsys.readouterr().out)

    assert human == data["lines"]


# ------------------------------------------------------------------------ ages


def test_a_window_is_labelled_the_way_a_person_reads_it():
    assert quota._window_label(43200) == "30d"
    assert quota._window_label(720) == "12h"
    assert quota._window_label(90) == "90m"
    assert quota._window_label(None) == "?"


def test_an_age_is_short_and_never_says_zero_minutes():
    assert quota.age_text(0) == "<1m"
    assert quota.age_text(240) == "4m"
    assert quota.age_text(3 * 3600 + 12 * 60) == "3h 12m"
    assert quota.age_text(2 * 86400 + 4 * 3600) == "2d 4h"


def test_a_percentage_keeps_one_decimal_only_when_it_has_one():
    assert quota.percent_text(59.0) == "59%"
    assert quota.percent_text(59.4) == "59.4%"


# ------------------------------------------------------------------------- cli


def test_the_verb_answers_on_the_sandbox_engine(sandbox):
    codex_reading(sandbox.home)

    done = run_quota(sandbox, "--json")

    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert any(row["provider"] == "Codex (free)" for row in data["rows"])
    assert data["lines"]


def test_the_verb_prints_one_line_per_row_without_flags(sandbox):
    codex_reading(sandbox.home)

    done = run_quota(sandbox)

    assert done.returncode == 0, done.stderr
    assert "Codex (free): 59% of the 30d window used" in done.stdout
    assert "Antigravity: not measured" in done.stdout
