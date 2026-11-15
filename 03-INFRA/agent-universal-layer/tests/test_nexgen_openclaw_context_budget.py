"""Observable tests for OpenClaw context budget reading and doctor/smoke checks.

nexgen: one-job reason="prove context budget reads transcripts tolerantly, catches over-budget sessions, and smoke/doctor report honestly"
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexgen_core import i18n  # noqa: E402
from nexgen_core.checks.context_budget_checks import check_context_budget  # noqa: E402
from nexgen_core.openclaw.context_budget import (  # noqa: E402
    find_recent_claude_sessions,
    read_claude_first_prompt,
    read_codex_first_prompt,
    session_started_at,
    transcript_started_since,
)
from nexgen_core.openclaw.smoke import run_smoke  # noqa: E402
from nexgen_core.openclaw_service import RunResult  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    """The lines below are English by construction, whatever the machine's locale is."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _assistant_line(
    input_tokens: int = 10,
    cache_creation: int = 20,
    cache_read: int = 30,
    msg_id: str = "msg-1",
    at: str | None = None,
) -> str:
    """A JSON line with type assistant and usage fields, optionally stamped like a real one."""
    entry: dict = {
        "type": "assistant",
        "message": {
            "id": msg_id,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            }
        }
    }
    if at is not None:
        entry["timestamp"] = at
    return json.dumps(entry) + "\n"


def _a_session_file(folder: Path, name: str, tokens: int, started: float) -> Path:
    """A transcript that started at `started` (epoch seconds) and first cost `tokens`."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(started, tz=UTC).isoformat().replace("+00:00", "Z")
    path = folder / name
    path.write_text(
        json.dumps({"type": "queue-operation", "timestamp": stamp}) + "\n"
        + _assistant_line(tokens, 0, 0, at=stamp),
        encoding="utf-8",
    )
    return path


def test_claude_first_prompt_sum_and_repeated_message_id(tmp_path):
    """The first prompt size is the sum of the three fields on the FIRST assistant message; repeated id is counted once."""
    transcript = tmp_path / "session.jsonl"
    content = (
        json.dumps({"type": "user", "text": "ciao"}) + "\n"
        + _assistant_line(input_tokens=1000, cache_creation=2000, cache_read=3000, msg_id="first-id")
        + _assistant_line(input_tokens=1000, cache_creation=2000, cache_read=3000, msg_id="first-id")  # streaming repeat
        + _assistant_line(input_tokens=500, cache_creation=500, cache_read=500, msg_id="second-id")
    )
    transcript.write_text(content, encoding="utf-8")

    tokens = read_claude_first_prompt(transcript)
    assert tokens == 6000  # 1000 + 2000 + 3000


def test_corrupt_line_is_tolerated(tmp_path):
    """A corrupt JSON line is skipped and reading continues."""
    transcript = tmp_path / "session.jsonl"
    content = (
        "corrupt json line here\n"
        + '{"truncated": true\n'
        + _assistant_line(input_tokens=50, cache_creation=50, cache_read=50)
    )
    transcript.write_text(content, encoding="utf-8")

    assert read_claude_first_prompt(transcript) == 150


def test_empty_file_returns_none(tmp_path):
    """An empty file or a file without assistant message returns None without error."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert read_claude_first_prompt(empty) is None

    no_assistant = tmp_path / "no_assistant.jsonl"
    no_assistant.write_text(json.dumps({"type": "user"}) + "\n", encoding="utf-8")
    assert read_claude_first_prompt(no_assistant) is None


def test_non_openclaw_slug_is_ignored(tmp_path):
    """Only projects whose slug names an OpenClaw workspace are collected."""
    claude_dir = tmp_path / ".claude"
    projects = claude_dir / "projects"
    
    # OpenClaw slug
    oc_slug = projects / "-home-openclaw--openclaw-workspace-automazioni"
    oc_slug.mkdir(parents=True)
    (oc_slug / "session1.jsonl").write_text(_assistant_line(100, 200, 300), encoding="utf-8")

    # Regular slug (not OpenClaw)
    other_slug = projects / "-home-user-project-personal"
    other_slug.mkdir(parents=True)
    (other_slug / "session2.jsonl").write_text(_assistant_line(500, 500, 500), encoding="utf-8")

    readings = find_recent_claude_sessions(claude_dir, now=time.time())
    assert len(readings) == 1
    assert readings[0].slug == "-home-openclaw--openclaw-workspace-automazioni"
    assert readings[0].tokens == 600


def test_doctor_check_outcomes(tmp_path):
    """BROKEN over budget, OK under it, and silent when nothing was measured all day."""
    claude_dir = tmp_path / ".claude"
    projects = claude_dir / "projects"
    oc_slug = projects / "-home-openclaw--openclaw-workspace"
    oc_slug.mkdir(parents=True)
    codex_dir = tmp_path / "codex"          # empty, and explicit so the machine's own CODEX_HOME is not read

    # 1. Nothing measured: a machine that did not chat through OpenClaw has nothing to report.
    assert check_context_budget(home=tmp_path, claude_dir=claude_dir, codex_dir=codex_dir) is None

    # 2. Under budget
    session = oc_slug / "session_under.jsonl"
    session.write_text(_assistant_line(10_000, 20_000, 30_000), encoding="utf-8")  # 60,000 tokens
    outcome_ok = check_context_budget(home=tmp_path, claude_dir=claude_dir, codex_dir=codex_dir, budget=150_000)
    assert outcome_ok.severity == Severity.OK
    assert "60,000" in outcome_ok.message

    # 3. Over budget
    session_over = oc_slug / "session_over.jsonl"
    session_over.write_text(_assistant_line(50_000, 100_000, 50_000), encoding="utf-8")  # 200,000 tokens
    outcome_broken = check_context_budget(home=tmp_path, claude_dir=claude_dir, codex_dir=codex_dir, budget=150_000)
    assert outcome_broken.severity == Severity.BROKEN
    assert "200,000" in outcome_broken.message
    assert "150,000" in outcome_broken.message
    assert "setup" in outcome_broken.action


def test_the_doctor_reads_codex_sessions_where_codex_writes_them(tmp_path, monkeypatch):
    """`$CODEX_HOME` moves the rollouts: measuring `~/.codex` reports nothing instead."""
    codex_home = tmp_path / "elsewhere"
    rollout_dir = codex_home / "sessions" / "2026" / "11" / "15"
    rollout_dir.mkdir(parents=True)
    (rollout_dir / "rollout-1.jsonl").write_text(
        json.dumps({"type": "session_meta",
                    "payload": {"cwd": "/home/user/.openclaw/workspace"}}) + "\n"
        + json.dumps({"type": "token_usage_record",
                      "payload": {"usage": {"input_tokens": 200_000, "cached_input_tokens": 190_000}}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    outcome = check_context_budget(home=tmp_path, claude_dir=tmp_path / "no-claude", budget=150_000)

    assert outcome is not None
    assert outcome.severity == Severity.BROKEN
    assert "200,000" in outcome.message


def test_a_token_field_that_is_not_a_number_does_not_crash_the_report(tmp_path):
    """A future CLI writing `"n/a"` is a reading the reader cannot use, never a crash."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant",
                    "message": {"id": "m", "usage": {"input_tokens": "n/a",
                                                     "cache_creation_input_tokens": 500}}}) + "\n",
        encoding="utf-8",
    )

    assert read_claude_first_prompt(transcript) == 500


def test_codex_reader_tolerantly_extracts_tokens(tmp_path):
    """Codex session rollout token_usage_record is parsed when present, or returns None."""
    codex_file = tmp_path / "rollout-123.jsonl"
    codex_file.write_text(json.dumps({
        "timestamp": "2026-11-14T10:00:00Z",
        "type": "token_usage_record",
        "payload": {
            "usage": {
                "input_tokens": 15000,
                "cached_input_tokens": 5000,
                "cache_write_input_tokens": 1000,
            }
        }
    }) + "\n", encoding="utf-8")

    # 16,000: `input_tokens` already contains the 5,000 cached ones, so they are not added again.
    assert read_codex_first_prompt(codex_file) == 16000

    empty_codex = tmp_path / "rollout-empty.jsonl"
    empty_codex.write_text(json.dumps({"type": "session_meta"}) + "\n", encoding="utf-8")
    assert read_codex_first_prompt(empty_codex) is None


def test_a_session_is_dated_by_its_first_record(tmp_path):
    """Appending to an old chat does not make it the session this turn started."""
    claude_dir = tmp_path / ".claude"
    oc_slug = claude_dir / "projects" / "-home-openclaw--openclaw-workspace"
    started = datetime(2026, 11, 15, 9, 0, tzinfo=UTC).timestamp()
    path = _a_session_file(oc_slug, "old.jsonl", 1000, started)

    assert session_started_at(path) == started
    assert transcript_started_since(claude_dir, started - 5) is not None
    assert transcript_started_since(claude_dir, started + 5) is None


def test_the_smoke_measures_the_session_the_turn_started_not_the_newest_file(tmp_path, capsys):
    """A chat that keeps writing during the smoke owns the newest mtime, not the turn."""
    claude_dir = tmp_path / ".claude"
    oc_slug = claude_dir / "projects" / "-home-openclaw--openclaw-workspace"
    oc_slug.mkdir(parents=True)
    turn_start = time.time()
    # Somebody else's busy chat: huge first prompt, started long before the turn.
    other = _a_session_file(oc_slug, "other-chat.jsonl", 300_000, turn_start - 3600)

    def write_the_turns_own_session():
        _a_session_file(oc_slug, "this-turn.jsonl", 30_000, time.time())
        # Written last, so an mtime-based reader would pick it: this is the trap.
        other.write_text(other.read_text(encoding="utf-8"), encoding="utf-8")

    runner = SmokeMock(on_turn=write_the_turns_own_session)
    code = run_smoke(run=runner, health_fn=lambda: {"status": "ok"}, claude_dir=claude_dir, budget=150_000)

    out = capsys.readouterr().out
    assert code == 0, out
    assert "30,000" in out
    assert "300,000" not in out


def test_the_smoke_tells_a_missing_transcript_from_another_runtime(tmp_path, monkeypatch, capsys):
    """The two situations get different sentences: one is a gap, the other is not Claude Code."""
    claude_dir = tmp_path / ".claude"
    (claude_dir / "projects" / "-home-openclaw--openclaw-workspace").mkdir(parents=True)
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(json.dumps({
        "agents": {
            "defaults": {"models": {"openai/gpt-6-luna": {"agentRuntime": {"id": "codex"}}}},
            "entries": {"main": {"model": "openai/gpt-6-luna"}},
        },
    }), encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(config_path))

    code = run_smoke(run=SmokeMock(), health_fn=lambda: {"status": "ok"}, claude_dir=claude_dir)

    out = capsys.readouterr().out
    assert code == 0
    assert "does not run on Claude Code" in out

    config_path.write_text(json.dumps({
        "agents": {
            "defaults": {"models": {"anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}}}},
            "entries": {"main": {"model": "anthropic/claude-opus-5-5"}},
        },
    }), encoding="utf-8")
    code = run_smoke(run=SmokeMock(), health_fn=lambda: {"status": "ok"}, claude_dir=claude_dir)

    out = capsys.readouterr().out
    assert code == 0
    assert "no transcript for this turn" in out


class SmokeMock:
    def __init__(self, on_turn=None):
        self.on_turn = on_turn

    def __call__(self, argv, timeout):
        if argv[:2] == ["openclaw", "--version"]:
            return RunResult(0, "OpenClaw 2026.10.6\n", "")
        if argv[:2] == ["openclaw", "config"]:
            return RunResult(0, "", "")
        if argv[:2] == ["openclaw", "mcp"]:
            return RunResult(0, "ok\n", "")
        if argv[:2] == ["openclaw", "agent"]:
            if self.on_turn:
                self.on_turn()
            return RunResult(0, "ok\n", "")
        return RunResult(127, "", "")


def test_smoke_three_cases(tmp_path, capsys):
    """Smoke test prints PASS when under budget, FAIL when over, and not measured when no transcript."""
    claude_dir = tmp_path / ".claude"
    oc_slug = claude_dir / "projects" / "-home-openclaw--openclaw-workspace"
    oc_slug.mkdir(parents=True)

    # Case 1: Probed agent did not run on Claude Code (no new transcript)
    runner1 = SmokeMock()
    code1 = run_smoke(run=runner1, health_fn=lambda: {"status": "ok"}, claude_dir=claude_dir, budget=150_000)
    assert code1 == 0
    out1 = capsys.readouterr().out
    assert "not measured" in out1 or "non misurato" in out1
    assert "FAIL" not in out1

    # Case 2: New transcript written under budget
    def write_under():
        _a_session_file(oc_slug, f"session_{time.time()}.jsonl", 30_000, time.time())

    runner2 = SmokeMock(on_turn=write_under)
    code2 = run_smoke(run=runner2, health_fn=lambda: {"status": "ok"}, claude_dir=claude_dir, budget=150_000)
    assert code2 == 0
    out2 = capsys.readouterr().out
    assert "PASS" in out2
    assert "30,000" in out2

    # Case 3: New transcript written over budget
    def write_over():
        _a_session_file(oc_slug, f"session_{time.time()}_over.jsonl", 300_000, time.time())

    runner3 = SmokeMock(on_turn=write_over)
    code3 = run_smoke(run=runner3, health_fn=lambda: {"status": "ok"}, claude_dir=claude_dir, budget=150_000)
    assert code3 == 1
    out3 = capsys.readouterr().out
    assert "FAIL" in out3
    assert "300,000" in out3


def test_a_budget_over_the_ceiling_is_a_warning_when_the_run_is_not_about_it(tmp_path, capsys):
    """The release gate keeps the measurement and drops the verdict: no rollback for a token count."""
    claude_dir = tmp_path / ".claude"
    oc_slug = claude_dir / "projects" / "-home-openclaw--openclaw-workspace"
    oc_slug.mkdir(parents=True)

    def write_over():
        _a_session_file(oc_slug, "session.jsonl", 300_000, time.time())

    runner = SmokeMock(on_turn=write_over)
    code = run_smoke(run=runner, health_fn=lambda: {"status": "ok"}, claude_dir=claude_dir,
                     budget=150_000, budget_decides=False)

    out = capsys.readouterr().out
    assert code == 0, out
    assert "WARN" in out
    assert "300,000" in out
    assert "FAIL" not in out
