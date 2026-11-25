"""Test comportamentali del guardiano dei turni `supervision_reaper` con processi reali."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from nexgen_local import supervision_reaper as reaper

_BASE_PYTHON = getattr(sys, "_base_executable", sys.executable)

_PARENT_SCRIPT = (
    "import pathlib, subprocess, sys, time\n"
    "pid_file = pathlib.Path(sys.argv[1])\n"
    "sleep_s = float(sys.argv[2])\n"
    "child = subprocess.Popen(\n"
    "    [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
    "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
    ")\n"
    "tmp = pid_file.with_suffix('.tmp')\n"
    "tmp.write_text(str(child.pid), encoding='utf-8')\n"
    "tmp.replace(pid_file)\n"
    "time.sleep(sleep_s)\n"
)


def _is_pid_alive(pid: int) -> bool:
    """Whether `pid` is still running (on POSIX, zombies count as finished)."""
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_for_child_pid(pid_file: Path, timeout_s: float = 10.0) -> int:
    """Wait until the spawned parent has written its child's pid atomically."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pid_file.is_file():
            text = pid_file.read_text(encoding="utf-8").strip()
            if text.isdigit():
                return int(text)
        time.sleep(0.05)
    raise AssertionError(f"il padre non ha scritto il pid del figlio entro {timeout_s}s")


@pytest.fixture
def tracked_procs():
    """Record spawned Popen handles and raw pids and force-kill survivors on teardown."""
    procs: list[subprocess.Popen] = []
    pids: list[int] = []
    pid_files: list[Path] = []

    def track_proc(proc: subprocess.Popen) -> subprocess.Popen:
        procs.append(proc)
        return proc

    def track_pid(pid: int) -> int:
        pids.append(pid)
        return pid

    def track_pid_file(path: Path) -> Path:
        pid_files.append(path)
        return path

    yield SimpleNamespace(proc=track_proc, pid=track_pid, pid_file=track_pid_file)

    for pf in pid_files:
        if pf.is_file():
            text = pf.read_text(encoding="utf-8").strip()
            if text.isdigit():
                pids.append(int(text))
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                proc.kill()
                proc.wait(timeout=3)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            pass
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass


def _spawn_parent_with_child(tmp_path: Path, tracked_procs, sleep_s: float) -> tuple[subprocess.Popen, int, float]:
    """Start a parent Python process that spawns a 60s child, writes its pid, then sleeps `sleep_s`."""
    pid_file = tracked_procs.pid_file(tmp_path / "child.pid")
    parent = tracked_procs.proc(
        reaper._REAL_POPEN(
            [_BASE_PYTHON, "-c", _PARENT_SCRIPT, str(pid_file), str(sleep_s)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **({"start_new_session": True} if os.name != "nt" else {}),
        )
    )
    created = psutil.Process(parent.pid).create_time()
    child_pid = tracked_procs.pid(_wait_for_child_pid(pid_file))
    return parent, child_pid, created


def test_orphan_child_is_reaped_after_parent_exits(tmp_path, tracked_procs):
    """Un figlio orfano lasciato vivo da un padre che esce viene fermato al termine del padre."""
    parent, child_pid, created = _spawn_parent_with_child(tmp_path, tracked_procs, sleep_s=3.0)

    result = reaper.reap(parent.pid, created, timeout_s=30, poll_s=0.2, grace_s=2)

    assert result["reason"] == "finito"
    assert child_pid in result["killed"]
    assert not _is_pid_alive(child_pid)


def test_expired_turn_stops_parent_and_child(tmp_path, tracked_procs):
    """Alla scadenza del timeout sia il padre che il figlio vengono terminati."""
    parent, child_pid, created = _spawn_parent_with_child(tmp_path, tracked_procs, sleep_s=60.0)

    result = reaper.reap(parent.pid, created, timeout_s=2.0, poll_s=0.2, grace_s=2)

    assert result["reason"] == "scaduto"
    assert not _is_pid_alive(parent.pid)
    assert not _is_pid_alive(child_pid)


def test_recycled_pid_is_never_touched(tracked_procs):
    """Se `create_time` non coincide con `created`, `reap` risponde sconosciuto e lascia vivo il processo."""
    sleeper = tracked_procs.proc(
        reaper._REAL_POPEN(
            [_BASE_PYTHON, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    )

    result = reaper.reap(sleeper.pid, created=0.0, timeout_s=5.0, poll_s=0.1, grace_s=1.0)

    assert result["reason"] == "sconosciuto"
    assert result["killed"] == []
    assert result["spared"] == []
    assert _is_pid_alive(sleeper.pid)


def test_shared_server_child_is_spared(tmp_path, tracked_procs, monkeypatch):
    """Un figlio riconosciuto da `is_shared_server` resta vivo e compare in `spared`."""
    parent, child_pid, created = _spawn_parent_with_child(tmp_path, tracked_procs, sleep_s=60.0)
    monkeypatch.setattr(reaper.processes, "is_shared_server", lambda row: row.get("pid") == child_pid)

    result = reaper.reap(parent.pid, created, timeout_s=1.0, poll_s=0.2, grace_s=2.0)

    assert result["reason"] == "scaduto"
    assert child_pid in result["spared"]
    assert child_pid not in result["killed"]
    assert not _is_pid_alive(parent.pid)
    assert _is_pid_alive(child_pid)


def test_watch_ignores_fake_popen_doubles(monkeypatch):
    """Un oggetto che non è un vero `subprocess.Popen` non avvia nulla e ritorna stringa vuota."""
    monkeypatch.setattr(
        reaper.subprocess, "Popen",
        lambda *args, **kwargs: pytest.fail("subprocess.Popen non va chiamato per un finto"),
    )
    before = {child.pid for child in psutil.Process(os.getpid()).children(recursive=True)}

    out = reaper.watch("t", "u", SimpleNamespace(pid=os.getpid()))

    after = {child.pid for child in psutil.Process(os.getpid()).children(recursive=True)}
    assert out == ""
    assert after == before


def test_real_guardian_reaps_orphan_in_background(tmp_path, tracked_procs, monkeypatch):
    """Il guardiano staccato lanciato da `watch` elimina il figlio orfano entro 20 secondi."""
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path))
    parent, child_pid, _ = _spawn_parent_with_child(tmp_path, tracked_procs, sleep_s=3.0)

    err = reaper.watch("task-test-finto", "unita-finta", parent, timeout_s=15.0)
    assert err == ""

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and _is_pid_alive(child_pid):
        time.sleep(0.2)

    assert not _is_pid_alive(child_pid)
