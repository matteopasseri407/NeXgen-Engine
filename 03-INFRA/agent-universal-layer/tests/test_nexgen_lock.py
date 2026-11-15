"""Unit tests for the host-wide lock: timeout validation and contention."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.lock import DEFAULT_TIMEOUT_SECONDS, HostLock, LockIOError, LockTimeoutError  # noqa: E402

#: A real second process, because the host rule is about processes and not about objects.
_HOLD_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[2])
from nexgen_core.lock import HostLock

lock = HostLock(lock_path=sys.argv[1], timeout=5)
lock.acquire()
print("held", flush=True)
sys.stdin.readline()
lock.release()
"""

pytestmark = pytest.mark.filterwarnings("ignore")


def test_non_finite_timeout_falls_back_to_default(tmp_path: Path, monkeypatch) -> None:
    """NaN/Inf timeouts must never cause an infinite poll or an infinite wait."""
    for bad in ("nan", "NaN", "inf", "-inf", "abc", ""):
        monkeypatch.setenv("AGENT_SYNC_LOCK_TIMEOUT_SECONDS", bad)
        assert HostLock(lock_path=tmp_path / "a.lock").timeout == DEFAULT_TIMEOUT_SECONDS
    monkeypatch.setenv("AGENT_SYNC_LOCK_TIMEOUT_SECONDS", "5")
    assert HostLock(lock_path=tmp_path / "a.lock").timeout == 5.0


def test_lock_contention_reports_and_releases(tmp_path: Path) -> None:
    """Two holders serialize: the second waits, then proceeds after release."""
    path = tmp_path / "guard.lock"
    first = HostLock(lock_path=path, timeout=5)
    first.acquire()
    try:
        second = HostLock(lock_path=path, timeout=0.1)
        with pytest.raises(LockTimeoutError):
            second.acquire()
    finally:
        first.release()
    third = HostLock(lock_path=path, timeout=5)
    third.acquire()
    third.release()


def test_unwritable_lock_dir_becomes_typed_error(tmp_path: Path, monkeypatch) -> None:
    """A state dir that cannot be created must surface as a lock error,
    never as a bare OSError halfway through a cycle."""
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(LockIOError):
        HostLock(lock_path=tmp_path / "nope" / "a.lock", timeout=1).acquire()


def test_guard_lock_contention_is_silent_success() -> None:
    err = LockTimeoutError("busy", Path("/tmp/x.lock"), is_guard=True)
    assert err.exit_code == 0
    manual = LockTimeoutError("busy", Path("/tmp/x.lock"), is_guard=False)
    assert manual.exit_code == 75


def test_a_lock_nobody_may_open_is_an_error_not_contention(tmp_path: Path) -> None:
    """A lock that cannot be opened as a file (a directory sits on the path, as
    an ACL-denied or sudo-made lock would) is not "another sync is running".
    Retrying it until the timeout turned it into a guard contention, which
    exits 0 in silence, forever."""
    obstacle = tmp_path / "guard.lock"
    obstacle.mkdir()
    with pytest.raises(LockIOError) as error:
        HostLock(lock_path=obstacle, timeout=1, is_guard=True).acquire()
    assert error.value.exit_code == 1


def test_a_negative_timeout_tries_once_instead_of_waiting_forever(tmp_path: Path) -> None:
    """The locking library reads a negative timeout as "no deadline at all";
    this contract has always been the opposite, so a misconfigured caller can
    never wedge a terminal or a timer unit on a lock somebody else holds."""
    path = tmp_path / "guard.lock"
    with HostLock(lock_path=path, timeout=5):
        started = time.monotonic()
        with pytest.raises(LockTimeoutError):
            HostLock(lock_path=path, timeout=-1).acquire()
        assert time.monotonic() - started < 2.0


def test_a_second_process_cannot_take_the_lock_and_gets_it_once_released(tmp_path: Path) -> None:
    """The behaviour the whole host rule rests on, proved across process
    boundaries: a real child holds the file, this process is refused for its
    whole wait, and the lock is free again once the child exits."""
    path = tmp_path / "guard.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_SCRIPT, str(path), str(SCRIPTS_DIR)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with pytest.raises(LockTimeoutError):
            HostLock(lock_path=path, timeout=0.5).acquire()
        assert time.monotonic() - started >= 0.5
    finally:
        holder.communicate(input="", timeout=30)
    with HostLock(lock_path=path, timeout=5) as free:
        assert free.lock_path == path


@pytest.mark.skipif(sys.platform == "win32", reason="a held byte-range lock refuses a second reader on Windows")
def test_the_held_lock_names_its_holder(tmp_path: Path) -> None:
    """`docs/what-gets-written.md` promises the lock carries the holder's pid,
    command name and timestamp for whoever finds it stuck."""
    path = tmp_path / "guard.lock"
    with HostLock(lock_path=path, timeout=5, command_name="nexgen-sync-probe"):
        text = path.read_text(encoding="utf-8")
    assert f"pid={os.getpid()}" in text
    assert "command=nexgen-sync-probe" in text
