"""One way to write files: atomic renames, timestamped backups, one policy.

Writing a config is the most failure-sensitive thing this engine does --
a truncated file is a CLI that no longer starts -- and the mechanics used
to live in four places with subtly different semantics: the scheduler's
writer preserved file modes and retried Windows antivirus locks, the
renderer's rotated the last three backups, everyone else accumulated.
Any fix to one copy (a retry, a permission bit) silently missed the other
three. This module is the single implementation; every writer delegates
to it, keeping only its own naming/retention choice as parameters.
"""
from __future__ import annotations

import contextlib
import glob
import os
import re
import tempfile
import time
from pathlib import Path

from nexgen_core.i18n import t

#: Retry budget per filesystem operation. Locks
#: from antivirus/indexing usually clear in milliseconds; past this budget
#: the error is real and must surface, not be retried forever.
_RETRY_BUDGET_SECONDS = 0.8


def _retry_permission_error(operation):
    """Retry a transient file lock, never conceal a persistent denial."""
    deadline = time.monotonic() + _RETRY_BUDGET_SECONDS
    delay = 0.05
    while True:
        try:
            return operation()
        except PermissionError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay *= 2


def atomic_write_text(path: Path, text: str, *, preserve_mode: bool = True, exclusive: bool = False) -> None:
    """Write-then-rename: a crash mid-write never leaves a truncated file.

    Carries the permission bits across the rename (a config rewritten
    without its `0600` would silently widen who can read secrets; setuid /
    setgid / sticky bits are never carried: they describe execution, not
    readability, and inheriting them from a compromised source would
    escalate it) and retries transient `PermissionError`s (Windows locks
    from antivirus/indexing), which was previously only the scheduler's
    writer behavior.

    The complete temporary file is fsynced before publication; directory
    fsync is best-effort where the filesystem supports it. Exclusive
    creation publishes with a hard link and refuses an existing target.
    The temp name is unique per process, thread and call (never just the
    PID): two writers in one process, or a stale tmp from a crashed run,
    cannot silently clobber each other.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = None
    if preserve_mode:
        try:
            old_mode = _retry_permission_error(path.stat).st_mode & 0o777
        except FileNotFoundError:
            pass
    # mkstemp creates an exclusive, private file. Only this call's temporary
    # file belongs to us; a filename glob cannot establish ownership.
    fd, name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if old_mode is not None:
                os.chmod(tmp, old_mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _retry_permission_error(lambda: os.link(tmp, path) if exclusive else os.replace(tmp, path))
        # Some filesystems (and Windows) do not support directory fsync.
        with contextlib.suppress(OSError):
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        _retry_permission_error(lambda: tmp.unlink(missing_ok=True))


def secure_artifact(directory: Path, path: Path | None = None) -> None:
    """Establish privacy before writing: 0700 directory, 0600 file on POSIX.

    Permission failures surface. Windows uses the profile's inherited ACL;
    POSIX chmod cannot establish a Windows access-control policy.
    """
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(directory, 0o700)
        if path is not None:
            os.chmod(path, 0o600)


def write_private_text(path: Path, text: str, *, exclusive: bool = False) -> None:
    """Publish complete private bytes, optionally refusing an ID collision."""
    secure_artifact(path.parent)
    atomic_write_text(path, text, preserve_mode=False, exclusive=exclusive)


def _safe_tag(tag: str | None) -> str | None:
    """A tag that cannot escape the backup filename or its rotation glob."""
    if tag is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", tag).strip(".-")
    return cleaned or "untagged"


def backup_file(path: Path, *, tag: str | None = None, keep: int | None = None,
                text: str | None = None) -> Path | None:
    """Timestamped copy of an EXISTING file, made BEFORE any write.

    Every incident that justified this package started with a config file
    overwritten with nothing to recover from. No backup for a file that
    doesn't exist yet -- there's nothing to preserve.

    `tag` names the reason (`permissions`, `instructions`, ...), producing
    `<name>.pre-<tag>-<timestamp>.bak`; without it, `<name>.bak-<timestamp>`.
    An exclusive filename keeps simultaneous backups distinct. `keep`
    rotates: only that many newest
    backups survive (the MCP renderer keeps 3); without it backups
    accumulate and their cleanup stays the user's (see `docs/uninstall.md`).
    ``text`` snapshots bytes already read by a caller instead of rereading
    a live file that may have changed; that snapshot is published privately.
    """
    import shutil

    path = Path(path)
    if text is None and not path.is_file():
        return None
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
    safe = _safe_tag(tag)
    stem = f"{path.name}.pre-{safe}-{stamp}" if safe else f"{path.name}.bak-{stamp}"
    fd, name = tempfile.mkstemp(prefix=stem + "-", suffix=".bak", dir=path.parent)
    os.close(fd)
    backup_path = Path(name)
    try:
        if text is None:
            shutil.copy2(path, backup_path)
        else:
            atomic_write_text(backup_path, text, preserve_mode=False)
    except BaseException:
        backup_path.unlink(missing_ok=True)
        raise
    if keep is not None:
        _prune_backups(path, safe, keep)
    return backup_path


def _prune_backups(path: Path, safe_tag: str | None, keep: int) -> None:
    """Drops rotated-out backups, oldest first. Runs only after the new
    content is safely on disk: pruning before a write that then fails
    would delete history and deliver nothing."""
    if safe_tag:
        matches = sorted(path.parent.glob(f"{glob.escape(path.name)}.pre-{safe_tag}-*.bak"))
    else:
        matches = sorted(path.parent.glob(f"{glob.escape(path.name)}.bak-*"))
    for old in matches[: max(len(matches) - keep, 0)]:
        old.unlink(missing_ok=True)


def write_text_if_changed(
    path: Path, text: str, *, tag: str | None = None, keep: int | None = None
) -> bool:
    """Backup (policy as in :func:`backup_file`) and atomic-write, but only
    when the content actually differs.

    The guard cycle runs twice an hour: rewriting a byte-identical file
    every cycle changes mtimes and piles up backups for nothing. Returns
    True when it wrote.
    """
    path = Path(path)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except UnicodeDecodeError as exc:
            # The file exists but cannot be read as text: overwriting it
            # would destroy content nobody inspected. Fail closed instead
            # of publishing new bytes over an unknown original.
            raise OSError(t("Refusing to overwrite unreadable file {path}: {exc}", path=path, exc=exc)) from exc
        except OSError:
            # Unreadable for permissions: the write below would fail too,
            # but with the original already backed up. Let it surface there.
            pass
    backup_file(path, tag=tag)
    atomic_write_text(path, text)
    if keep is not None:
        _prune_backups(path, _safe_tag(tag), keep)
    return True
