"""Unit tests for the host-wide lock: timeout validation and contention."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

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
