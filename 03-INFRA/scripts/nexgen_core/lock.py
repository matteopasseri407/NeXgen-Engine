"""Gestione del lock esclusivo host-wide cross-platform (Linux & Windows).

Preserva il contratto operativo:
- Una sola operazione di sincronizzazione / guardia / pubblicazione per host.
- Timeout configurabile (default 30 secondi via AGENT_SYNC_LOCK_TIMEOUT_SECONDS).
- Contesa su 'guard': uscita pulita con codice 0 (un'altra operazione è già in corso).
- Contesa su operazioni manuali ('apply', 'publish', 'vault-push'): uscita con codice 75.
"""
from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Iterator

EXIT_BUSY_MANUAL = 75
EXIT_BUSY_GUARD = 0
DEFAULT_TIMEOUT_SECONDS = 30.0


class LockTimeoutError(TimeoutError):
    """Sollevata quando il lock non può essere acquisito entro il timeout."""
    def __init__(self, message: str, lock_path: Path, is_guard: bool = False):
        super().__init__(message)
        self.lock_path = lock_path
        self.is_guard = is_guard
        self.exit_code = EXIT_BUSY_GUARD if is_guard else EXIT_BUSY_MANUAL


class HostLock:
    """Lock esclusivo a livello di host."""

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
                state_dir = Path(os.environ.get("AGENT_STATE_DIR", Path.home() / ".nexgen-engine"))
                self.lock_path = state_dir / "agent-sync.lock"
        else:
            self.lock_path = Path(lock_path)

        if timeout is None:
            env_timeout = os.environ.get("AGENT_SYNC_LOCK_TIMEOUT_SECONDS")
            self.timeout = float(env_timeout) if env_timeout else DEFAULT_TIMEOUT_SECONDS
        else:
            self.timeout = float(timeout)

        self.is_guard = is_guard
        self.command_name = command_name
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Tenta di acquisire il lock entro il tempo di timeout."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.time()

        while True:
            try:
                # Apertura in lettura/scrittura (creazione se non esiste)
                self._fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)
                if self._try_lock(self._fd):
                    # Scrive i metadati del processo corrente
                    try:
                        os.ftruncate(self._fd, 0)
                        os.lseek(self._fd, 0, os.SEEK_SET)
                        meta = f"pid={os.getpid()}\ncommand={self.command_name}\ntimestamp={time.time()}\n"
                        os.write(self._fd, meta.encode("utf-8"))
                    except OSError:
                        pass
                    return True
                else:
                    os.close(self._fd)
                    self._fd = None
            except OSError:
                if self._fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(self._fd)
                    self._fd = None

            elapsed = time.time() - start_time
            if elapsed >= self.timeout:
                msg = f"Impossibile acquisire il lock '{self.lock_path}' dopo {self.timeout:.1f}s (processo attivo in corso)."
                raise LockTimeoutError(msg, self.lock_path, self.is_guard)

            time.sleep(0.5)

    def release(self) -> None:
        """Rilascia il lock e chiude il descrittore di file."""
        if self._fd is not None:
            try:
                self._unlock(self._fd)
            finally:
                with contextlib.suppress(OSError):
                    os.close(self._fd)
                self._fd = None

    def _try_lock(self, fd: int) -> bool:
        """Tentativo non bloccante di lock dipendente dal sistema operativo."""
        if sys.platform == "win32":
            import msvcrt
            try:
                if os.fstat(fd).st_size == 0:
                    with contextlib.suppress(OSError):
                        os.write(fd, b"0")
                        os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            except (OSError, PermissionError):
                return False
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (OSError, BlockingIOError):
                return False

    def _unlock(self, fd: int) -> None:
        """Sblocco dipendente dal sistema operativo."""
        if sys.platform == "win32":
            import msvcrt
            with contextlib.suppress(OSError, PermissionError):
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)

    def __enter__(self) -> HostLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


@contextlib.contextmanager
def locked_operation(
    lock_path: Path | str | None = None,
    timeout: float | None = None,
    is_guard: bool = False,
    command_name: str = "agent-sync"
) -> Iterator[HostLock]:
    """Helper context manager per eseguire un blocco di codice sotto lock."""
    lock = HostLock(lock_path=lock_path, timeout=timeout, is_guard=is_guard, command_name=command_name)
    try:
        lock.acquire()
        yield lock
    finally:
        lock.release()
