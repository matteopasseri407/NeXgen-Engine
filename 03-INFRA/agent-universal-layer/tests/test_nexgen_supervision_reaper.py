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

from nexgen_local import supervision_idle
from nexgen_local import supervision_reaper as reaper
from nexgen_local import supervision_state as state

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


def test_signs_of_life_change_when_the_copy_changes(tmp_path):
    """Un file scritto nella copia è un segno di vita, anche senza output del processo."""
    folder = tmp_path / "copia"
    folder.mkdir()
    before = supervision_idle.signs_of_life(os.getpid(), folder, [])

    (folder / "resoconto.md").write_text("lavoro\n", encoding="utf-8")

    assert supervision_idle.signs_of_life(os.getpid(), folder, []) != before


def test_a_turn_with_no_sign_of_life_is_stopped_for_idleness(tracked_procs):
    """Una guardia che non vede muoversi niente chiude il turno e il suo albero, come alla scadenza."""
    sleeper = tracked_procs.proc(
        reaper._REAL_POPEN(
            [_BASE_PYTHON, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **({"start_new_session": True} if os.name != "nt" else {}),
        )
    )
    created = psutil.Process(sleeper.pid).create_time()

    result = reaper.reap(
        sleeper.pid, created, timeout_s=30, poll_s=0.1, grace_s=1.0,
        idle_s=1.0, progress=lambda: 0, probe_s=0.05,
    )

    assert result["reason"] == reaper.IDLE_REASON
    assert result["idle_s"] >= 1.0
    assert not _is_pid_alive(sleeper.pid)


def test_a_turn_that_keeps_moving_runs_until_its_timeout(tracked_procs):
    """Finché i segni di vita cambiano, l'inattività non chiude il turno: lo chiude la scadenza."""
    sleeper = tracked_procs.proc(
        reaper._REAL_POPEN(
            [_BASE_PYTHON, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **({"start_new_session": True} if os.name != "nt" else {}),
        )
    )
    created = psutil.Process(sleeper.pid).create_time()
    readings = iter(range(10_000))

    result = reaper.reap(
        sleeper.pid, created, timeout_s=1.0, poll_s=0.05, grace_s=1.0,
        idle_s=0.5, progress=lambda: next(readings), probe_s=0.05,
    )

    assert result["reason"] == "scaduto"
    assert not _is_pid_alive(sleeper.pid)


def test_an_idle_turn_is_retried_once_and_only_then_marked_stalled(tmp_path, monkeypatch):
    """Il primo silenzio rilancia, il secondo scrive lo stallo e avvisa: nessun harness, nessun megafono vero."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    task = state.start("prova inattiva")["id"]
    calls: list = []
    alerted: list = []

    def fake_reap(*_args, **_kwargs):
        return {"reason": reaper.IDLE_REASON, "idle_s": 900.0, "killed": [], "spared": [], "refused": []}

    def fake_relaunch(task_id, unit, launch):
        calls.append("relaunch")
        return SimpleNamespace(pid=222)

    def fake_hold(task_id, unit, proc):
        calls.append(("hold", getattr(proc, "pid", None)))

    def alert(unit, session, minutes):
        alerted.append((unit, session, minutes))

    supervision_idle.guard(
        task, "prova", 111, 0.0, timeout_s=1, idle_s=900, launch={"folder": str(tmp_path)},
        reap_fn=fake_reap, relaunch_fn=fake_relaunch, hold_fn=fake_hold, alert_fn=alert,
    )

    events = [row["event"] for row in state.read_events(task)]
    assert calls == ["relaunch", ("hold", 222)]
    assert "turn_idle" in events and "turn_idle_relaunch" in events
    assert "unit_stalled" not in events
    assert alerted == []

    supervision_idle.guard(
        task, "prova", 222, 0.0, timeout_s=1, idle_s=900, launch={"folder": str(tmp_path)},
        attempt=2, reap_fn=fake_reap, relaunch_fn=fake_relaunch, hold_fn=fake_hold, alert_fn=alert,
    )

    events = [row["event"] for row in state.read_events(task)]
    stalled = [row for row in state.read_events(task) if row["event"] == "unit_stalled"]
    assert "unit_stalled" in events
    assert stalled[-1]["reason"] == "inattivita"
    assert stalled[-1]["threshold"] == 15.0
    assert alerted == [("prova", "", 15.0)]
    assert calls == ["relaunch", ("hold", 222)], "il secondo silenzio non rilancia"


def test_the_stall_alert_carries_the_measured_minutes(tmp_path, monkeypatch):
    """L'avviso arriva con i minuti dentro, non con le graffe del modello: un megafono finto, mai quello vero."""
    from nexgen_core import megaphone as megaphone_mod

    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    task = state.start("prova inattiva")["id"]
    sent: list[dict] = []

    class FakeMegaphone:
        def send_alert(self, **kwargs):
            sent.append(kwargs)
            return True

    monkeypatch.setattr(megaphone_mod, "Megaphone", FakeMegaphone)
    stopped = {"reason": reaper.IDLE_REASON, "idle_s": 900.0, "killed": [], "spared": [], "refused": []}

    supervision_idle.guard(
        task, "prova", 111, 0.0, timeout_s=1, idle_s=900, launch=None, attempt=2,
        reap_fn=lambda *a, **k: stopped,
    )

    assert sent, "nessun avviso mandato"
    assert "{" not in sent[0]["message"] and "15" in sent[0]["message"]
    assert sent[0]["alert_key"] == "turn_idle:prova"


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


def test_a_few_hundredths_of_cpu_are_not_a_sign_of_life(monkeypatch):
    """A hung process ticks its event loop: counted to the hundredth it would never go idle."""
    spent = iter([1.20, 1.23, 1.31])
    monkeypatch.setattr(supervision_idle, "_cpu_of", lambda _proc: next(spent))
    monkeypatch.setattr(supervision_idle.psutil, "Process",
                        lambda _pid: SimpleNamespace(children=lambda recursive: []))

    readings = {supervision_idle._tree_cpu(1) for _ in range(3)}

    assert len(readings) == 1


def test_a_long_prompt_reaches_the_guardian_through_a_file(monkeypatch, tmp_path):
    """A brief past the Windows command-line limit must not leave the turn unguarded."""
    started: list[list[str]] = []

    class Launched(reaper._REAL_POPEN):
        def __init__(self, argv, **_kwargs):
            started.append(list(argv))

    proc = Launched.__new__(Launched)
    proc.pid = os.getpid()
    monkeypatch.setattr(reaper, "_REAL_POPEN", lambda argv, **kwargs: started.append(list(argv)))
    monkeypatch.setattr(reaper, "watchable", lambda _proc: True)
    log = tmp_path / "task" / "60-stderr-s.log"
    prompt = "x" * 40_000

    reaper.watch("t", "u", proc, idle_s=60, launch={"prompt": prompt, "logs": [str(log)]})

    argv = started[-1]
    assert all(len(part) < 1000 for part in argv)
    recipe = supervision_idle.recipe_from_file(argv[argv.index("--launch-file") + 1])
    assert recipe["prompt"] == prompt
