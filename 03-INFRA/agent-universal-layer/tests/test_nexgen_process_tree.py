"""force_stop_process_tree: an exited launcher on Windows still owns live children.

A `cmd.exe` that started an npm shim can return while the Node children it
spawned keep pipes and sockets open. The early return for an already exited
launcher is a POSIX concern (with no group there is nothing left to signal);
on Windows the tree kill must still run.

The same rule in the Council seat cleanup (ricognizione 5): it decided on
`proc.poll()` and `_council_process_group`, and on Windows the group never
exists, so an exited launcher skipped the tree kill entirely.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import seat_process  # noqa: E402
from nexgen_core import processes  # noqa: E402


class _ExitedLauncher:
    """A launcher that already returned, like cmd.exe after a shim start."""

    pid = 4242

    def __init__(self) -> None:
        self.killed = False
        self.waited = False

    def poll(self):
        return 0

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


class _RecordingRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")


def _fake_platform(monkeypatch, name: str) -> _RecordingRun:
    """Patch the module's own `os`/`subprocess`, never the real os.name."""
    runs = _RecordingRun()
    monkeypatch.setattr(processes, "os", types.SimpleNamespace(name=name))
    monkeypatch.setattr(processes, "subprocess", types.SimpleNamespace(
        run=runs, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    return runs


def test_exited_launcher_on_windows_still_kills_the_tree(monkeypatch):
    """MUST-04: while the launcher is gone, its children are not; taskkill /T must run."""
    runs = _fake_platform(monkeypatch, "nt")
    proc = _ExitedLauncher()

    assert processes.force_stop_process_tree(proc, process_group=None) is True

    assert runs.calls, "taskkill was never attempted for an exited launcher with live children"
    argv = runs.calls[0]
    assert argv[:2] == ["taskkill.exe", "/PID"] and "4242" in argv and "/T" in argv and "/F" in argv, argv
    assert proc.waited


def test_exited_launcher_without_group_on_posix_still_returns_early(monkeypatch):
    """With no POSIX group and a gone launcher there is nothing to signal."""
    runs = _fake_platform(monkeypatch, "posix")
    proc = _ExitedLauncher()

    assert processes.force_stop_process_tree(proc, process_group=None) is True

    assert runs.calls == []
    assert not proc.killed and not proc.waited


def test_run_seat_cleanup_taskkills_after_the_windows_launcher_exited(tmp_path, monkeypatch):
    """Ricognizione 5: la stessa regola nel finally di run_seat. Il launcher e'
    uscito, ma su Windows i suoi figli possono essere ancora vivi: il cleanup
    non deve saltare `_stop_process_tree_with_telemetry`."""
    proc = _ExitedLauncher()
    stopped: list = []
    monkeypatch.setattr(seat_process, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(seat_process, "_stop_process_tree_with_telemetry",
                        lambda p, sink, *, reason: stopped.append((p, reason)))
    monkeypatch.setattr(seat_process, "_build_seat_command", lambda *_a: types.SimpleNamespace(
        argv=[], stdin_text=None, output_file=None, input_file=None, scratch=None))

    def fail_before_spawning(_prepared, runtime):
        runtime.proc = proc
        runtime.proc_token = "proc-test"
        raise seat_process.SeatRunError("[council] sintetico", "process_error")

    monkeypatch.setattr(seat_process, "_run_with_timeout", fail_before_spawning)

    with pytest.raises(seat_process.SeatRunError):
        seat_process.run_seat({"cli": "ollama", "model": "offline-test"}, "", tmp_path, 5)

    assert stopped == [(proc, "cleanup")]
