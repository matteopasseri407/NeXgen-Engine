"""Pin read-only shutdown telemetry for seat processes (fake procs only).

Covers: a faked close records the method used and the outcome, a fallback
close records survivors after the wait, and the existing run_seat result
carries the telemetry as an optional extra field without changing its
shape. Every spawned process is an innocuous ``python -u`` printer with a
short timeout; every test asserts the process registry is empty at the end.
No real vendor CLI is ever invoked and no real process is ever killed by
these tests (the two unit tests below never spawn at all: they drive the
telemetry wrapper with a FakeProc double and a stubbed stop function).
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


class _FakeProc:
    """Minimal Popen surface the telemetry wrapper observes.

    Never a real process: poll/kill/wait are pure in-memory state flips.
    """

    def __init__(self, *, alive: bool = True, pid: int | None = 12345, group: int | None = None):
        self._alive = alive
        self.pid = pid
        if group is not None:
            self._council_process_group = group
        self.returncode: int | None = None

    def poll(self):
        return None if self._alive else self.returncode

    def kill(self):  # pragma: no cover - the stop function is stubbed in these tests
        self._alive = False
        self.returncode = -9

    def wait(self, timeout=None):
        if self._alive:
            import subprocess

            raise subprocess.TimeoutExpired([], timeout)
        return self.returncode


def _stub_stop(reaped_returncode: int | None, *, reap: bool, calls: list):
    """Fake _force_stop_process_tree: records the call, flips fake state."""

    def _fake(proc):
        calls.append(proc)
        if reap:
            proc._alive = False
            proc.returncode = reaped_returncode
            return True
        return False

    return _fake


def test_fake_close_records_method_and_outcome(monkeypatch):
    """A faked close records which method applied and what it achieved."""
    calls: list = []
    monkeypatch.setattr(seat_process, "_force_stop_process_tree", _stub_stop(0, reap=True, calls=calls))
    proc = _FakeProc(alive=True, group=9999)
    events: list = []
    event = seat_process._stop_process_tree_with_telemetry(proc, events, reason="streaming_timeout")

    assert calls == [proc]
    expected_method = "killpg" if os.name == "posix" else "taskkill"
    assert event["method"] == expected_method
    assert event["reason"] == "streaming_timeout"
    assert event["alive_before"] is True
    assert event["stopped"] is True
    assert event["returncode"] == 0
    assert event["survivors_after_wait"] is False
    assert events == [event]


def test_fallback_close_records_survivors(monkeypatch):
    """An unreaped fallback close records the survivors after the wait."""
    calls: list = []
    monkeypatch.setattr(seat_process, "_force_stop_process_tree", _stub_stop(None, reap=False, calls=calls))
    # No pid and no process group: neither taskkill nor killpg can apply.
    proc = _FakeProc(alive=True, pid=None)
    events: list = []
    event = seat_process._stop_process_tree_with_telemetry(proc, events, reason="post_eof_hung")

    assert calls == [proc]
    assert event["method"] == "fallback"
    assert event["reason"] == "post_eof_hung"
    assert event["stopped"] is False
    assert event["returncode"] is None
    assert event["survivors_after_wait"] is True
    assert event["needs_retry"] is (os.name == "nt")
    assert events == [event]


def _fake_provider_invocation(monkeypatch, tmp_path, source: str):
    script = tmp_path / "fake_provider.py"
    script.write_text(source, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env["PYTHONUTF8"] = "1"
    invocation = SeatInvocation([sys.executable, "-u", str(script)], None, None, None, env, tmp_path)
    monkeypatch.setattr(seat_process, "_build_seat_command", lambda *args: invocation)


def test_success_result_carries_shutdown_telemetry(monkeypatch, tmp_path):
    _fake_provider_invocation(monkeypatch, tmp_path, "print('VERDICT: APPROVE')\n")
    answer, usage = run_seat({"cli": "ollama", "model": "offline-test"}, "", tmp_path, 5)

    assert "VERDICT: APPROVE" in answer
    telemetry = usage.get("shutdown_telemetry")
    assert isinstance(telemetry, list) and telemetry
    required = {"reason", "method", "returncode", "survivors_after_wait", "needs_retry"}
    for event in telemetry:
        assert required <= set(event)
        assert event["method"] in {"killpg", "taskkill", "fallback"}
    assert session._live_procs_snapshot() == []


def test_timeout_error_carries_shutdown_telemetry(monkeypatch, tmp_path):
    _fake_provider_invocation(monkeypatch, tmp_path, "import time\ntime.sleep(30)\n")
    with pytest.raises(SeatRunError) as error:
        run_seat({"cli": "ollama", "model": "offline-test"}, "", tmp_path, 1)

    assert error.value.kind == "no_output_timeout"
    telemetry = getattr(error.value, "shutdown_telemetry", None)
    assert isinstance(telemetry, list) and telemetry
    assert telemetry[0]["reason"] == "streaming_timeout"
    assert telemetry[0]["alive_before"] is True
    assert telemetry[0]["survivors_after_wait"] is False
    assert session._live_procs_snapshot() == []
