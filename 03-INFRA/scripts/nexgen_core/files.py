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
import os
import re
import time
from pathlib import Path

from nexgen_core.i18n import t

#: How long the Windows retry loop waits in total before giving up. Locks
#: from antivirus/indexing usually clear in milliseconds; past this budget
#: the error is real and must surface, not be retried forever.
_RETRY_BUDGET_SECONDS = 0.8


def atomic_write_text(path: Path, text: str, *, preserve_mode: bool = True) -> None:
    """Write-then-rename: a crash mid-write never leaves a truncated file.

    Carries the permission bits across the rename (a config rewritten
    without its `0600` would silently widen who can read secrets; setuid /
    setgid / sticky bits are never carried: they describe execution, not
    readability, and inheriting them from a compromised source would
    escalate it) and retries transient `PermissionError`s (Windows locks
    from antivirus/indexing), which was previously only the scheduler's
    writer behavior.

    Crash-safe for real: the temp file is fsynced before the rename and
    the directory after it, so a power loss cannot publish an empty file.
    The temp name is unique per process, thread and call (never just the
    PID): two writers in one process, or a stale tmp from a crashed run,
    cannot silently clobber each other.
    """
    import secrets
    import threading

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = None
    if preserve_mode and path.exists():
        try:
            old_mode = path.stat().st_mode & 0o777
        except OSError:
            pass
    token = f"{os.getpid()}-{threading.get_ident()}-{secrets.token_hex(4)}"
    tmp = path.with_name(f"{path.name}.{token}.tmp")
    # Best effort: drop OUR OWN stale tmps from crashed runs, never fail.
    # Only files older than a few minutes: a fresh tmp belongs to a live
    # writer (possibly another thread of this same process), and sweeping
    # it would manufacture the exact clobbering this unique name prevents.
    cutoff = time.time() - 300
    for stale in path.parent.glob(f"{path.name}.*.tmp"):
        if stale == tmp:
            continue
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            continue
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    if old_mode is not None:
        try:
            os.chmod(tmp, old_mode)
        except OSError:
            pass
    delay = 0.05
    while True:
        try:
            os.replace(tmp, path)
            try:
                dir_fd = os.open(path.parent, os.O_RDONLY)
            except OSError:
                return
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
            return
        except PermissionError:
            if delay >= _RETRY_BUDGET_SECONDS:
                raise
            time.sleep(delay)
            delay *= 2


def _safe_tag(tag: str | None) -> str | None:
    """A tag that cannot escape the backup filename or its rotation glob."""
    if tag is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", tag).strip(".-")
    return cleaned or "untagged"


def backup_file(path: Path, *, tag: str | None = None, keep: int | None = None) -> Path | None:
    """Timestamped copy of an EXISTING file, made BEFORE any write.

    Every incident that justified this package started with a config file
    overwritten with nothing to recover from. No backup for a file that
    doesn't exist yet -- there's nothing to preserve.

    `tag` names the reason (`permissions`, `instructions`, ...), producing
    `<name>.pre-<tag>-<timestamp>.bak`; without it, `<name>.bak-<timestamp>`.
    The stamp carries seconds plus PID, so two backups in the same second
    never overwrite each other. `keep` rotates: only that many newest
    backups survive (the MCP renderer keeps 3); without it backups
    accumulate and their cleanup stays the user's (see `docs/uninstall.md`).
    """
    import shutil

    path = Path(path)
    if not path.is_file():
        return None
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    safe = _safe_tag(tag)
    stem = f"{path.name}.pre-{safe}-{stamp}.bak" if safe else f"{path.name}.bak-{stamp}"
    backup_path = path.with_name(stem)
    shutil.copy2(path, backup_path)
    if keep is not None:
        _prune_backups(path, safe, keep)
    return backup_path


def _prune_backups(path: Path, safe_tag: str | None, keep: int) -> None:
    """Drops rotated-out backups, oldest first. Runs only after the new
    content is safely on disk: pruning before a write that then fails
    would delete history and deliver nothing."""
    if safe_tag:
        matches = sorted(path.parent.glob(f"{path.name}.pre-{safe_tag}-*.bak"))
    else:
        matches = sorted(path.parent.glob(f"{path.name}.bak-*"))
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
