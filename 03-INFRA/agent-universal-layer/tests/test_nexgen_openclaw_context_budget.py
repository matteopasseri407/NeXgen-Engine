"""Observable tests for OpenClaw context budget reading and doctor/smoke checks.

nexgen: one-job reason="prove context budget reads transcripts tolerantly, catches over-budget sessions, and smoke/doctor report honestly"
"""
from __future__ import annotations

import json
import time
from nexgen_core.checks.context_budget_checks import check_context_budget  # noqa: E402
from nexgen_core.openclaw.context_budget import (  # noqa: E402
    find_recent_claude_sessions,
    read_claude_first_prompt,
    read_codex_first_prompt,
)
from nexgen_core.openclaw.smoke import run_smoke  # noqa: E402
from nexgen_core.openclaw_service import RunResult  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402


def _assistant_line(
    input_tokens: int = 10,
    cache_creation: int = 20,
    cache_read: int = 30,
    msg_id: str = "msg-1",
) -> str:
    """A JSON line with type assistant and usage fields."""
    return json.dumps({
        "type": "assistant",
        "message": {
            "id": msg_id,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            }
        }
    }) + "\n"


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
    """Doctor check returns BROKEN when over budget, OK when under, and UNDETERMINED when nothing measured."""
    claude_dir = tmp_path / ".claude"
    projects = claude_dir / "projects"
    oc_slug = projects / "-home-openclaw--openclaw-workspace"
    oc_slug.mkdir(parents=True)

    # 1. Nothing measured
    outcome_empty = check_context_budget(home=tmp_path, claude_dir=claude_dir)
    assert outcome_empty.severity == Severity.UNDETERMINED
    assert "openclaw.context_budget" == outcome_empty.id

    # 2. Under budget
    session = oc_slug / "session_under.jsonl"
    session.write_text(_assistant_line(10_000, 20_000, 30_000), encoding="utf-8")  # 60,000 tokens
    outcome_ok = check_context_budget(home=tmp_path, claude_dir=claude_dir, budget=150_000)
    assert outcome_ok.severity == Severity.OK
    assert "60,000" in outcome_ok.message

    # 3. Over budget
    session_over = oc_slug / "session_over.jsonl"
    session_over.write_text(_assistant_line(50_000, 100_000, 50_000), encoding="utf-8")  # 200,000 tokens
    outcome_broken = check_context_budget(home=tmp_path, claude_dir=claude_dir, budget=150_000)
    assert outcome_broken.severity == Severity.BROKEN
    assert "200,000" in outcome_broken.message
    assert "150,000" in outcome_broken.message
    assert "setup" in outcome_broken.action


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

    assert read_codex_first_prompt(codex_file) == 21000

    empty_codex = tmp_path / "rollout-empty.jsonl"
    empty_codex.write_text(json.dumps({"type": "session_meta"}) + "\n", encoding="utf-8")
    assert read_codex_first_prompt(empty_codex) is None


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
    projects = claude_dir / "projects"
    oc_slug = projects / "-home-openclaw--openclaw-workspace"
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
        f = oc_slug / f"session_{time.time()}.jsonl"
        f.write_text(_assistant_line(10_000, 10_000, 10_000), encoding="utf-8")  # 30,000 tokens

    runner2 = SmokeMock(on_turn=write_under)
    code2 = run_smoke(run=runner2, health_fn=lambda: {"status": "ok"}, claude_dir=claude_dir, budget=150_000)
    assert code2 == 0
    out2 = capsys.readouterr().out
    assert "PASS" in out2
    assert "30,000" in out2

    # Case 3: New transcript written over budget
    def write_over():
        f = oc_slug / f"session_{time.time()}_over.jsonl"
        f.write_text(_assistant_line(100_000, 100_000, 100_000), encoding="utf-8")  # 300,000 tokens

    runner3 = SmokeMock(on_turn=write_over)
    code3 = run_smoke(run=runner3, health_fn=lambda: {"status": "ok"}, claude_dir=claude_dir, budget=150_000)
    assert code3 == 1
    out3 = capsys.readouterr().out
    assert "FAIL" in out3
    assert "300,000" in out3
