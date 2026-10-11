"""Spawned children must not flash a console on Windows.

A console child of a process without its own console (the OpenCode
desktop app, MCP gateways, scheduled tasks) otherwise gets a visible
window that steals focus on every spawn: once per agent tool call
through the guardrail consults and the lazy MCP servers.

These tests never fake the platform: flipping ``os.name`` process-wide
changes the ``pathlib`` flavour for everything built meanwhile
(``PosixPath`` on a Windows runner), which poisons pytest's own path
handling. The Windows-only behavior is asserted on Windows and skipped
elsewhere.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

from nexgen_core import mcp_check, scheduler  # noqa: E402
from nexgen_core.processes import no_window_kwargs  # noqa: E402

windows_only = pytest.mark.skipif(os.name != "nt", reason="hidden consoles are a Windows behavior")


def test_no_window_kwargs_match_the_platform():
    kwargs = no_window_kwargs()
    if os.name == "nt":
        assert kwargs.get("creationflags", 0) & 0x08000000
        assert "startupinfo" in kwargs
    else:
        assert kwargs == {}


@windows_only
def test_scheduler_external_calls_stay_windowless(monkeypatch):
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    scheduler._run_external(["schtasks.exe", "/Query", "/TN", "synthetic"], timeout=1)
    assert captured.get("creationflags", 0) & 0x08000000
    assert "startupinfo" in captured


@windows_only
def test_mcp_check_session_stays_windowless(monkeypatch):
    captured: dict = {}

    class _FakeProc:
        def __init__(self, argv, **kwargs):
            captured.update(kwargs)
            self.stdout = iter(())
            self.stdin = io.BytesIO()

        def poll(self):
            return 0

    monkeypatch.setattr(mcp_check.subprocess, "Popen", _FakeProc)
    mcp_check._Session(["synthetic"], {}, None)
    assert captured.get("creationflags", 0) & 0x08000000
    assert "startupinfo" in captured


@windows_only
def test_lazy_server_spawn_stays_windowless(monkeypatch):
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "mcp" / "lazy-mcp.py"
    spec = importlib.util.spec_from_file_location("mcp_no_window_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured: dict = {}

    class _FakeProc:
        def __init__(self, argv, **kwargs):
            captured.update(kwargs)
            self.pid = 1
            self.stderr = io.BytesIO(b"")

        def poll(self):
            return None

    monkeypatch.setattr(module.subprocess, "Popen", _FakeProc)
    handle = module._ServerHandle("synthetic", {"command": sys.executable, "args": ["-c", "pass"]})
    try:
        handle._start_stdio()
    finally:
        handle.proc = None
    assert captured.get("creationflags", 0) & 0x08000000
    assert "startupinfo" in captured


@windows_only
def test_council_seat_spawn_stays_windowless(monkeypatch, tmp_path):
    import seat_process
    from seat_process import SeatInvocation, run_seat

    script = tmp_path / "provider.py"
    script.write_text("print('VERDICT: APPROVE')\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env["PYTHONUTF8"] = "1"
    invocation = SeatInvocation([sys.executable, "-u", str(script)], "", None, None, env, tmp_path)
    monkeypatch.setattr(seat_process, "_build_seat_command", lambda *args: invocation)
    real_popen = seat_process.subprocess.Popen
    captured: dict = {}

    def spy(argv, **kwargs):
        captured.update(kwargs)
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(seat_process.subprocess, "Popen", spy)
    answer, _ = run_seat({"cli": "ollama", "model": "offline-test"}, "", tmp_path, 30)
    assert "VERDICT: APPROVE" in answer
    assert captured.get("creationflags", 0) & 0x08000000
    assert "startupinfo" in captured
