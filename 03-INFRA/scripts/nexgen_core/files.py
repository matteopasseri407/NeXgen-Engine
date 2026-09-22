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

import os
import time
from pathlib import Path

#: How long the Windows retry loop waits in total before giving up. Locks
#: from antivirus/indexing usually clear in milliseconds; past this budget
#: the error is real and must surface, not be retried forever.
_RETRY_BUDGET_SECONDS = 0.8


def atomic_write_text(path: Path, text: str, *, preserve_mode: bool = True) -> None:
    """Write-then-rename: a crash mid-write never leaves a truncated file.

    Carries the file mode across the rename (a config rewritten without
    its `0600` would silently widen who can read secrets) and retries
    transient `PermissionError`s (Windows locks from antivirus/indexing),
    which was previously only the scheduler's writer behavior.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = None
    if preserve_mode and path.exists():
        try:
            old_mode = path.stat().st_mode
        except OSError:
            pass
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    if old_mode is not None:
        try:
            os.chmod(tmp, old_mode)
        except OSError:
            pass
    delay = 0.05
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if delay >= _RETRY_BUDGET_SECONDS:
                raise
            time.sleep(delay)
            delay *= 2


def backup_file(path: Path, *, tag: str | None = None, keep: int | None = None) -> Path | None:
    """Timestamped copy of an EXISTING file, made BEFORE any write.

    Every incident that justified this package started with a config file
    overwritten with nothing to recover from. No backup for a file that
    doesn't exist yet -- there's nothing to preserve.

    `tag` names the reason (`permissions`, `instructions`, ...), producing
    `<name>.pre-<tag>-<timestamp>.bak`; without it, `<name>.bak-<timestamp>`.
    `keep` rotates: only that many newest backups survive (the MCP renderer
    keeps 3); without it backups accumulate and their cleanup stays the
    user's (see `docs/uninstall.md`).
    """
    import shutil

    path = Path(path)
    if not path.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = f"{path.name}.pre-{tag}-{stamp}.bak" if tag else f"{path.name}.bak-{stamp}"
    backup_path = path.with_name(stem)
    shutil.copy2(path, backup_path)
    if keep is not None:
        if tag:
            matches = sorted(path.parent.glob(f"{path.name}.pre-{tag}-*.bak"))
        else:
            matches = sorted(path.parent.glob(f"{path.name}.bak-*"))
        for old in matches[: max(len(matches) - keep, 0)]:
            old.unlink(missing_ok=True)
    return backup_path


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
        except (OSError, UnicodeDecodeError):
            pass
    backup_file(path, tag=tag, keep=keep)
    atomic_write_text(path, text)
    return True
