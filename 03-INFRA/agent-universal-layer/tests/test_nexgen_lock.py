"""Unit tests for the host-wide lock: timeout validation and contention."""
from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import lock  # noqa: E402
from nexgen_core.lock import DEFAULT_TIMEOUT_SECONDS, HostLock, LockIOError, LockTimeoutError  # noqa: E402

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


def test_open_held_by_another_process_is_retried_not_fatal(tmp_path: Path, monkeypatch) -> None:
    """MUST-03: on Windows Defender or the indexer can hold the just-opened
    lock file (WinError 32 arrives as PermissionError). That is contention,
    not an inaccessible lock: retry inside the budget and acquire."""
    real_open = os.open
    attempts = {"count": 0}

    def flaky_open(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise PermissionError(errno.EACCES, "The process cannot access the file (WinError 32)")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(lock.os, "open", flaky_open)
    monkeypatch.setattr(lock.time, "sleep", lambda _seconds: None)
    handle = HostLock(lock_path=tmp_path / "guard.lock", timeout=5)
    handle.acquire()
    try:
        assert attempts["count"] == 3, "the lock must be retried, not abandoned"
    finally:
        handle.release()
    assert handle._fd is None


def test_non_retryable_open_error_still_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """Only share violations are retried: any other open failure stays a typed error."""
    def broken_open(*args, **kwargs):
        raise OSError(errno.EINVAL, "synthetic invalid argument")

    monkeypatch.setattr(lock.os, "open", broken_open)
    monkeypatch.setattr(lock.time, "sleep", lambda _seconds: None)
    with pytest.raises(LockIOError):
        HostLock(lock_path=tmp_path / "guard.lock", timeout=1).acquire()
