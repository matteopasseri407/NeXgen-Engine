"""Cross-platform (Linux & Windows) host-wide exclusive lock management.

Preserves the operational contract:
- A single sync / guard / publish operation per host.
- Configurable timeout (default 30 seconds via AGENT_SYNC_LOCK_TIMEOUT_SECONDS).
- Contention on 'guard': clean exit with code 0 (another operation is already running).
- Contention on manual operations ('apply', 'publish', 'vault-push'): exit with code 75.

The locking mechanism belongs to filelock (MIT, https://github.com/tox-dev/filelock):
on Windows it opens the file through NtCreateFile and takes the range with
LockFileEx, so a symlink or junction planted at the lock path is refused
rather than followed; on POSIX it takes an flock. On both, a lock file held
open for an instant by somebody else (Defender, the search indexer) is
retried as contention instead of failing the cycle. What stays in this
module is the policy: which contention is a silent guard skip, which is exit
75 for a person, and one lock per host.

Scope limit: this is a HOST lock, and the state dir must live on local
storage. On a network share (NFS/SMB) POSIX locks can be client-local, so
two hosts could both believe they hold it. Never point the state dir at a
share; the vault itself syncs through git, not through shared files.
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from nexgen_core.errors import NexgenError
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_state_dir

EXIT_BUSY_MANUAL = 75
EXIT_BUSY_GUARD = 0
DEFAULT_TIMEOUT_SECONDS = 30.0
LOCK_FILENAME = "agent-sync.lock"


class LockTimeoutError(NexgenError, TimeoutError):
    """Raised when the lock cannot be acquired within the timeout."""
    def __init__(self, message: str, lock_path: Path, is_guard: bool = False):
        super().__init__(message)
        self.lock_path = lock_path
        self.is_guard = is_guard
        self.exit_code = EXIT_BUSY_GUARD if is_guard else EXIT_BUSY_MANUAL


class LockIOError(NexgenError, OSError):
    """The lock is inaccessible, rather than held by another process."""
    exit_code = 1


class HostLock:
    """Host-level exclusive lock."""

    def __init__(
        self,
        lock_path: Path | str | None = None,
        timeout: float | None = None,
        is_guard: bool = False,
        command_name: str = "agent-sync"
    ) -> None:
        if lock_path is None:
            env_path = os.environ.get("AGENT_SYNC_LOCK_FILE")
            if env_path:
                self.lock_path = Path(env_path)
            else:
                state_dir = resolve_state_dir()
                self.lock_path = state_dir / "agent-sync.lock"
        else:
            self.lock_path = Path(lock_path)

        if timeout is None:
            env_timeout = os.environ.get("AGENT_SYNC_LOCK_TIMEOUT_SECONDS")
            try:
                parsed = float(env_timeout) if env_timeout else DEFAULT_TIMEOUT_SECONDS
            except (TypeError, ValueError):
                # A non-numeric value must never crash the command: fall back
                # to the default instead of dying in float().
                parsed = DEFAULT_TIMEOUT_SECONDS
            # NaN never satisfies `elapsed >= timeout` (infinite poll);
            # infinite waits forever; both come from a misconfigured env,
            # never from intent. Negative/zero stays: try once, fail fast.
            self.timeout = DEFAULT_TIMEOUT_SECONDS if not math.isfinite(parsed) else parsed
        else:
            self.timeout = float(timeout)
            if not math.isfinite(self.timeout):
                raise ValueError(t("Lock timeout must be finite."))

        self.is_guard = is_guard
        self.command_name = command_name
        self._lock: FileLock | None = None
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Tries to acquire the lock within the timeout."""
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LockIOError(
                t("Could not prepare the lock directory '{path}' ({error}).", path=self.lock_path.parent, error=exc),
            ) from exc

        # filelock reads a negative timeout as "wait forever" and 0 as "one
        # attempt"; this contract is "try once and fail fast" for both, so a
        # misconfigured caller never blocks a terminal or a timer unit.
        wait = max(self.timeout, 0.0)
        lock = FileLock(
            str(self.lock_path),
            timeout=wait,
            preserve_lock_file=True,
            on_acquired=self._record_holder,
        )
        try:
            lock.acquire(timeout=wait)
        except FileLockTimeout as exc:
            msg = t(
                "Could not acquire lock '{lock_path}' after {timeout:.1f}s "
                "(another sync is still running). Wait a minute and retry; "
                "if it keeps happening, look for a stuck 'nexgen sync' process.",
                lock_path=self.lock_path, timeout=self.timeout,
            )
            raise LockTimeoutError(msg, self.lock_path, self.is_guard) from exc
        except OSError as exc:
            raise LockIOError(t("Could not open lock {path}: {error}", path=self.lock_path, error=exc)) from exc
        self._lock = lock
        return True

    def _record_holder(self, fd: int) -> None:
        """Notes who holds the lock in the file, for the person reading it next.

        The descriptor is the one filelock locked, so the note lands in the
        locked inode without a second open, which on Windows would collide
        with the range lock just taken. A lock file that accepts the lock but
        not the note still locks: the note is a courtesy and never a reason
        to fail a cycle.
        """
        self._fd = fd
        meta = f"pid={os.getpid()}\ncommand={self.command_name}\ntimestamp={time.time()}\n"
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, meta.encode("utf-8"))
        except OSError:  # nexgen: allow reason="an unwritable lock file still serializes the host"
            pass

    def release(self) -> None:
        """Releases the lock, if this object still holds it."""
        lock = self._lock
        if lock is None:
            return
        try:
            lock.release()
        finally:
            self._lock = None
            self._fd = None

    def __enter__(self) -> HostLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


def host_mutation(
    command_name: str,
    *,
    state_dir: Path | str | None = None,
    timeout: float | None = None,
    is_guard: bool = False,
) -> HostLock:
    """The one lock every path that changes this machine's state goes through.

    The guard, vault-push, the unattended upgrade, the pin bump and
    `doctor --fix` each wrote to the same files (generated configs, the
    skill library, the engine checkout) while only some of them took the lock,
    so "one mutation per host" held for the guard and for nothing else.

    `state_dir` is for a caller built around another home (tests, a second
    profile); without it the machine's own state directory is used.
    `AGENT_SYNC_LOCK_FILE`, when set, wins for every caller alike: an operator
    who moves the lock moves it for all of them, not just for those that
    happened to leave the path implicit.

    Hold it only around the code that writes, never around a child process
    that will ask for it again: the lock is not re-entrant across processes.
    """
    override = os.environ.get("AGENT_SYNC_LOCK_FILE")
    if override:
        path = Path(override)
    elif state_dir is not None:
        path = Path(state_dir) / LOCK_FILENAME
    else:
        path = resolve_state_dir() / LOCK_FILENAME
    return HostLock(lock_path=path, timeout=timeout, is_guard=is_guard, command_name=command_name)
