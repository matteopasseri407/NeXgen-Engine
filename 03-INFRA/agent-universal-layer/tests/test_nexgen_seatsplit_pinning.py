"""Pin current seat-split behavior with fake seats only (never real CLIs).

Covers: model+prompt transport (stdin or protected file, never argv),
isolated env without secrets, timeout with recorded outcome, no orphans.
All spawned processes are innocuous ``python -u`` printers with short
timeouts; every test asserts the process registry is empty at the end.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import seat_process  # noqa: E402
import session  # noqa: E402
from seat_process import SeatInvocation, SeatRunError, run_seat  # noqa: E402


@pytest.fixture(autouse=True)
def _no_orphans():
    yield
    assert session._live_procs_snapshot() == []


def _cleanup_invocation(invocation: SeatInvocation) -> None:
    import shutil

    if invocation.output_file is not None:
        try:
            invocation.output_file.unlink(missing_ok=True)
        except OSError:
            pass
    if invocation.input_file is not None:
        try:
            invocation.input_file.unlink(missing_ok=True)
        except OSError:
            pass
    if invocation.scratch is not None:
        shutil.rmtree(invocation.scratch, ignore_errors=True)


def _fake_invocation(monkeypatch, tmp_path, source: str, prompt: str):
    """Patch _build_seat_command to spawn an innocuous python printer."""
    script = tmp_path / "fake_provider.py"
    script.write_text(source, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env["PYTHONUTF8"] = "1"
    invocation = SeatInvocation(
        [sys.executable, "-u", str(script)], prompt, None, None, env, tmp_path
    )
    monkeypatch.setattr(seat_process, "_build_seat_command", lambda *args: invocation)
    return invocation


def test_command_carries_model_and_prompt_off_argv(tmp_path):
    prompt = "SYNTHETIC-PROMPT-seatsplit-pinning-12345"
    seat = {"cli": "ollama", "model": "offline-test-model"}
    invocation = seat_process._build_seat_command(seat, prompt, tmp_path)
    try:
        assert any("offline-test-model" in arg for arg in invocation.argv)
        assert prompt not in " ".join(invocation.argv)
        assert invocation.stdin_text == prompt
        assert invocation.input_file is None
    finally:
        _cleanup_invocation(invocation)
    assert session._live_procs_snapshot() == []


def test_opencode_prompt_travels_via_protected_file(tmp_path):
    prompt = "SYNTHETIC-PROMPT-opencode-file-67890"
    seat = {"cli": "opencode", "model": "opencode/offline-test"}
    invocation = seat_process._build_seat_command(seat, prompt, tmp_path)
    try:
        assert any("offline-test" in arg for arg in invocation.argv)
        assert prompt not in " ".join(invocation.argv)
        assert invocation.stdin_text is None
        assert invocation.input_file is not None
        assert invocation.input_file.read_text(encoding="utf-8") == prompt
        assert str(invocation.input_file) in " ".join(invocation.argv)
    finally:
        _cleanup_invocation(invocation)
    assert session._live_procs_snapshot() == []


def test_isolated_env_holds_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("N8N_MCP_TOKEN", "synthetic-secret-token-abc")
    monkeypatch.setenv("VAULT_LIBRARY_TOKEN", "synthetic-secret-token-def")
    seat = {"cli": "codex", "model": "offline-test-model"}
    invocation = seat_process._build_seat_command(seat, "prompt", tmp_path)
    try:
        assert invocation.env is not None
        flat = " ".join(f"{k}={v}" for k, v in invocation.env.items())
        assert "synthetic-secret-token-abc" not in flat
        assert "synthetic-secret-token-def" not in flat
        assert "N8N_MCP_TOKEN" not in invocation.env
        assert "VAULT_LIBRARY_TOKEN" not in invocation.env
    finally:
        _cleanup_invocation(invocation)
    assert session._live_procs_snapshot() == []


def test_run_seat_success_records_verdict_and_cleans_up(tmp_path, monkeypatch):
    prompt = "SYNTHETIC-PROMPT-echo-24680"
    source = "import sys\ntext = sys.stdin.read()\nprint('VERDICT: APPROVE')\n"
    _fake_invocation(monkeypatch, tmp_path, source, prompt)
    seat = {"cli": "ollama", "model": "offline-test"}
    answer, _usage = run_seat(seat, prompt, tmp_path, 5)
    assert "VERDICT: APPROVE" in answer
    assert session._live_procs_snapshot() == []


def test_timeout_fires_with_recorded_outcome_and_no_orphan(tmp_path, monkeypatch):
    source = "import time\ntime.sleep(30)\n"
    _fake_invocation(monkeypatch, tmp_path, source, "")
    seat = {"cli": "ollama", "model": "offline-test"}
    with pytest.raises(SeatRunError) as error:
        run_seat(seat, "", tmp_path, 1)
    assert error.value.kind == "no_output_timeout"
    assert session._live_procs_snapshot() == []


def test_partial_output_timeout_is_distinguished_and_clean(tmp_path, monkeypatch):
    source = "import sys, time\nprint('partial bytes', flush=True)\ntime.sleep(30)\n"
    _fake_invocation(monkeypatch, tmp_path, source, "")
    seat = {"cli": "ollama", "model": "offline-test"}
    with pytest.raises(SeatRunError) as error:
        run_seat(seat, "", tmp_path, 1)
    assert error.value.kind == "partial_timeout"
    assert session._live_procs_snapshot() == []
