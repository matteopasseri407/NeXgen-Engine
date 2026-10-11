"""Spawned children must not flash a console on Windows.

A console child of a process without its own console (the OpenCode
desktop app, MCP gateways, scheduled tasks) otherwise gets a visible
window that steals focus on every spawn: once per agent tool call
through the guardrail consults and the lazy MCP servers.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

from nexgen_core import mcp_check, scheduler  # noqa: E402
from nexgen_core.processes import no_window_kwargs  # noqa: E402


def _assert_forwarded(captured: dict) -> None:
    expected = no_window_kwargs()
    assert captured.get("creationflags", 0) == expected.get("creationflags", 0)
    assert ("startupinfo" in captured) == ("startupinfo" in expected)


def test_no_window_kwargs_contract(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    hidden = no_window_kwargs()
    assert hidden.get("creationflags", 0) & 0x08000000
    monkeypatch.setattr(os, "name", "posix")
    assert no_window_kwargs() == {}


def test_scheduler_external_calls_stay_windowless(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    scheduler._run_external(["schtasks.exe", "/Query", "/TN", "synthetic"], timeout=1)
    _assert_forwarded(captured)


def test_mcp_check_session_stays_windowless(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
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
    _assert_forwarded(captured)


def test_lazy_server_spawn_stays_windowless(monkeypatch):
    import importlib.util

    monkeypatch.setattr(os, "name", "nt")
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
    _assert_forwarded(captured)


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
    _assert_forwarded(captured)
