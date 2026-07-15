"""agy is blocked as a PASSIVE Council seat (2026-07-15 live incident): see
AGY_BLOCK_REASON in council.py for the full finding. This file proves the
block holds at every entry point, and specifically that no `agy` process is
ever spawned -- not just that an error is raised somewhere upstream of it.
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

COUNCIL_PATH = Path(__file__).resolve().parents[1] / "council" / "council.py"


def load_council(monkeypatch, tmp_path):
    vault = tmp_path / "KnowledgeVault"
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", str(vault))
    module_name = f"council_agy_blocked_under_test_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(module_name, COUNCIL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    mod.SESSIONS_DIR = tmp_path / "sessions"
    mod.SEATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    return mod


def write_seats(council, text: str) -> None:
    content = text.strip()
    if not content.startswith("schema_version:"):
        content = "schema_version: 1\n" + content
    council.SEATS_PATH.write_text(content + "\n", encoding="utf-8")


def relay_args(**overrides):
    values = {
        "question": "Valuta questo piano sintetico",
        "context": None,
        "diff": None,
        "sequence": "reviewer=agy-seat",
        "max_seats": 5,
        "no_stats_precheck": True,
        "allow_training_risk": True,
        "keep_session": False,
        "timeout_seconds": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def single_mode_args(**overrides):
    """Minimal Namespace for resolve_seat(): only .seat and
    .allow_training_risk are actually read on this path."""
    values = {
        "seat": "agy-seat",
        "allow_training_risk": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def challenge_args(**overrides):
    """Mirrors the exact Namespace shape cmd_challenge needs (see the
    equivalent call in test_council_relay.py): 'plan', not 'question'."""
    values = {
        "plan": "Piano da stressare",
        "context": None,
        "seat": "agy-seat",
        "allow_training_risk": True,
        "keep_session": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _agy_seat_entry(name: str = "agy-seat") -> str:
    """A single seats.yaml seat entry at a fixed 2/4-space indent, meant to
    be concatenated with other entries under one 'seats:' key -- callers
    supply the 'seats:' header themselves so indentation always matches."""
    return (
        f"  {name}:\n"
        "    vendor: google\n"
        "    cli: agy\n"
        "    model: Gemini 3.1 Pro (High)\n"
        "    quota_pool: antigravity\n"
        "    zero_retention: false\n"
    )


def _agy_seat_yaml(name: str = "agy-seat") -> str:
    return "seats:\n" + _agy_seat_entry(name)


def _forbid_popen(monkeypatch) -> None:
    """Any call proves the guard failed to stop the spawn before it happened."""

    def _must_not_spawn(*args, **kwargs):
        raise AssertionError(f"subprocess.Popen must not be called for a blocked agy seat: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", _must_not_spawn)


def test_resolve_seat_refuses_an_explicit_agy_seat(monkeypatch, tmp_path):
    council = load_council(monkeypatch, tmp_path)
    write_seats(council, _agy_seat_yaml())
    _forbid_popen(monkeypatch)

    with pytest.raises(SystemExit, match="agy"):
        council.resolve_seat(single_mode_args())


def test_challenge_refuses_an_explicit_agy_seat_before_any_round(monkeypatch, tmp_path):
    council = load_council(monkeypatch, tmp_path)
    write_seats(council, _agy_seat_yaml())
    _forbid_popen(monkeypatch)
    monkeypatch.setattr(council, "egress_gate", lambda text: None)

    with pytest.raises(SystemExit, match="agy"):
        council.cmd_challenge(challenge_args())


def test_relay_skips_agy_candidate_and_uses_the_declared_fallback(monkeypatch, tmp_path):
    council = load_council(monkeypatch, tmp_path)
    fallback_entry = (
        "  fallback:\n"
        "    vendor: vendor-c\n"
        "    cli: opencode\n"
        "    model: opencode/fallback\n"
        "    quota_pool: opencode-free\n"
        "    zero_retention: true\n"
    )
    write_seats(council, "seats:\n" + _agy_seat_entry("agy-seat") + fallback_entry)
    monkeypatch.setattr(council, "egress_gate", lambda text: None)
    attempted_models = []

    def fake_run_seat(seat, prompt, session_dir, timeout_seconds=None):
        attempted_models.append(seat["model"])
        return "Risposta dal fallback\nVERDICT: APPROVE\n", {}

    monkeypatch.setattr(council, "run_seat", fake_run_seat)

    council.cmd_relay(relay_args(sequence="reviewer=agy-seat|fallback"))

    # The agy candidate must never reach run_seat at all -- only the fallback does.
    assert attempted_models == ["opencode/fallback"]


def test_relay_with_only_an_agy_candidate_fails_with_the_agy_reason(monkeypatch, tmp_path):
    council = load_council(monkeypatch, tmp_path)
    write_seats(council, _agy_seat_yaml())
    _forbid_popen(monkeypatch)
    monkeypatch.setattr(council, "egress_gate", lambda text: None)

    with pytest.raises(SystemExit, match="agy"):
        council.cmd_relay(relay_args(sequence="reviewer=agy-seat"))


def test_run_seat_refuses_agy_before_any_subprocess_is_spawned(monkeypatch, tmp_path):
    """The authoritative check: call run_seat directly (bypassing every
    upstream fail-fast checkpoint) and prove no process is ever built or
    spawned -- this is the guarantee GPT-5.6 Sol's review asked for, not
    merely 'an error is raised somewhere before the vendor CLI runs'."""
    council = load_council(monkeypatch, tmp_path)
    _forbid_popen(monkeypatch)
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    agy_seat = {
        "vendor": "google",
        "cli": "agy",
        "model": "Gemini 3.1 Pro (High)",
        "quota_pool": "antigravity",
        "zero_retention": False,
    }

    with pytest.raises(council.SeatRunError, match="agy") as excinfo:
        council.run_seat(agy_seat, "prompt qualsiasi", session_dir)

    assert excinfo.value.kind not in council.RETRYABLE_SEAT_ERROR_KINDS


def test_claude_opus_thinking_style_agy_seat_is_also_blocked(monkeypatch, tmp_path):
    """The block is keyed on cli == 'agy', not on which model string a
    particular agy-backed seat declares -- a second agy seat with a
    different declared model (mirrors seats.yaml's real claude-opus-thinking
    entry) must be refused the same way."""
    council = load_council(monkeypatch, tmp_path)
    _forbid_popen(monkeypatch)
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    other_agy_seat = {
        "vendor": "anthropic",
        "cli": "agy",
        "model": "Claude Opus 4.6 (Thinking)",
        "quota_pool": "antigravity",
        "zero_retention": False,
    }

    with pytest.raises(council.SeatRunError, match="agy"):
        council.run_seat(other_agy_seat, "prompt qualsiasi", session_dir)


def test_agy_transport_plumbing_still_builds_correctly_though_run_seat_refuses_it(monkeypatch, tmp_path):
    """run_seat refuses agy outright (tested above), but _build_seat_command
    itself was deliberately left intact rather than deleted -- it is the
    known-correct starting point if agy ever clears the reactivation bar in
    AGY_BLOCK_REASON. This calls it directly (never through run_seat, so the
    block above is irrelevant here) and checks the exact invariant the old
    parametrized transport test used to check for agy before it was
    excluded: a large prompt goes over stdin, never argv."""
    council = load_council(monkeypatch, tmp_path)
    prompt = "x" * 130_000
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    invocation = council._build_seat_command(
        {"cli": "agy", "model": "Gemini 3.1 Pro (High)"}, prompt, session_dir
    )

    assert all(prompt not in arg for arg in invocation.argv)
    assert invocation.stdin_text == prompt
    assert invocation.argv[:2] == ["agy", "--print"]
