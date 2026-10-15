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
import random
import re
import tempfile
import time
from pathlib import Path

from nexgen_core.i18n import t

#: Retry budget per filesystem operation. Locks from antivirus/indexing
#: usually clear in milliseconds, but a scan of a fresh file on a loaded
#: Windows box can hold it longer; past this budget the error is real and
#: must surface, not be retried forever.
_RETRY_BUDGET_SECONDS = 2.0


def _retry_permission_error(operation):
    """Retry a transient file lock, never conceal a persistent denial.

    The wait is jittered: writers that collided on one target (Windows denies
    a replace while another is in flight) would otherwise retry in lockstep
    and collide again until the budget ran out.
    """
    deadline = time.monotonic() + _RETRY_BUDGET_SECONDS
    delay = 0.05
    while True:
        try:
            return operation()
        except PermissionError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay * random.uniform(0.5, 1.5), remaining))
            delay = min(delay * 2, 0.4)


def atomic_write_text(
    path: Path, text: str, *, preserve_mode: bool = True, exclusive: bool = False, newline: str | None = None
) -> None:
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
    _atomic_publish(path, text, binary=False, preserve_mode=preserve_mode, exclusive=exclusive, newline=newline)


def atomic_write_bytes(path: Path, data: bytes, *, preserve_mode: bool = True) -> None:
    """`atomic_write_text` for content that must reach the disk byte for byte (a script, a binary)."""
    _atomic_publish(path, data, binary=True, preserve_mode=preserve_mode, exclusive=False, newline=None)


def _atomic_publish(
    path: Path, payload: str | bytes, *, binary: bool, preserve_mode: bool, exclusive: bool, newline: str | None
) -> None:
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
        opened = os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding="utf-8", newline=newline)
        with opened as handle:
            if old_mode is not None:
                os.chmod(tmp, old_mode)
            handle.write(payload)
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


def publish_symlink(link: Path, target: Path) -> None:
    """Make `link` point at `target` without ever leaving it missing.

    The link is built under a unique name beside the destination and renamed
    over it, so a failure (no symlink privilege on Windows, a full disk)
    leaves whatever was at `link` exactly as it was. Unlinking first and
    linking afterwards left a window with nothing there, and no way back
    when the second step failed. Raises OSError; the caller decides the
    fallback (a copy).
    """
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"{link.name}.", suffix=".lnk", dir=link.parent)
    os.close(fd)
    tmp = Path(name)
    tmp.unlink()
    try:
        tmp.symlink_to(target)
        _retry_permission_error(lambda: os.replace(tmp, link))
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


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


def _is_clean_vs_head(path: Path) -> bool:
    """True only when git proves the file is tracked AND unmodified vs HEAD.

    Every failure mode (no git binary, outside a repo, no commits yet,
    git error, timeout) returns False: a missed backup is unrecoverable,
    a redundant one is just disk debris. Note `git diff --quiet` alone is
    NOT proof -- it exits 0 for untracked files too -- so tracked-ness is
    checked separately with `ls-files --error-unmatch`.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        return False
    try:
        anchor = path if path.is_dir() else path.parent
        top = subprocess.run(
            ["git", "-C", str(anchor), "rev-parse", "--show-toplevel"],
            capture_output=True, stdin=subprocess.DEVNULL,
            encoding="utf-8", timeout=15,
        )
        if top.returncode != 0:
            return False
        rel = os.path.relpath(path, top.stdout.strip())
        if rel.startswith(".."):
            return False
        root = top.stdout.strip()
        tracked = subprocess.run(
            ["git", "-C", root, "ls-files", "--error-unmatch", "--", rel],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=15,
        )
        if tracked.returncode != 0:
            return False
        clean = subprocess.run(
            ["git", "-C", root, "diff", "--quiet", "HEAD", "--", rel],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=15,
        )
        return clean.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _backup_file_impl(path: Path, *, tag: str | None = None, keep: int | None = None,
                       text: str | None = None, skip_if_committed: bool = False) -> Path | None:
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
    ``skip_if_committed`` skips the backup when git proves the on-disk
    content is already committed (tracked and clean vs HEAD): the previous
    version is recoverable with `git show HEAD:<path>` on any machine, so a
    local copy only adds debris. Any doubt (untracked, dirty, no repo, git
    missing or failing) still takes the backup. Returns None when skipped,
    same as for a file that doesn't exist yet.
    """
    import shutil

    path = Path(path)
    if text is None and not path.is_file():
        return None
    if skip_if_committed and text is None and _is_clean_vs_head(path):
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
            # copy2 gives the backup the source's mode, which undoes the
            # private mkstemp file made above. Rendered configs hold bearer
            # tokens, so a backup must never be readable by more people than
            # the mkstemp default allows.
            if os.name != "nt":
                os.chmod(backup_path, 0o600)
        else:
            atomic_write_text(backup_path, text, preserve_mode=False)
    except BaseException:
        backup_path.unlink(missing_ok=True)
        raise
    if keep is not None:
        _prune_backups(path, safe, keep)
    return backup_path


def backup_file(path: Path, *, tag: str | None = None, keep: int | None = None,
                text: str | None = None, skip_if_committed: bool = False) -> Path | None:
    """Delega a :func:`_backup_file_impl`; firma e comportamento invariati."""
    return _backup_file_impl(path, tag=tag, keep=keep, text=text, skip_if_committed=skip_if_committed)


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


def _write_text_if_changed_impl(
    path: Path, text: str, *, tag: str | None = None, keep: int | None = None,
    skip_if_committed: bool = False,
) -> bool:
    """Backup (policy as in :func:`backup_file`) and atomic-write, but only
    when the content actually differs.

    The guard cycle runs twice an hour: rewriting a byte-identical file
    every cycle changes mtimes and piles up backups for nothing. Returns
    True when it wrote.
    """
    path = Path(path)
    # Editing a file means editing the file a symlink points to. Replacing the
    # link itself silently detached a dotfiles-managed config (~/.bashrc into a
    # dotfiles repository) from the place its owner keeps it. The backup stays
    # beside the link, so it never lands inside someone's dotfiles repository.
    try:
        target = path.resolve() if path.is_symlink() else path
    except RuntimeError as exc:  # symlink loop
        raise OSError(t("Refusing to write through a symlink loop at {path}", path=path)) from exc
    if target.is_file():
        try:
            if target.read_text(encoding="utf-8") == text:
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
    backup_file(path, tag=tag, skip_if_committed=skip_if_committed)
    atomic_write_text(target, text)
    if keep is not None:
        _prune_backups(path, _safe_tag(tag), keep)
    return True


def write_text_if_changed(
    path: Path, text: str, *, tag: str | None = None, keep: int | None = None,
    skip_if_committed: bool = False,
) -> bool:
    """Delega a :func:`_write_text_if_changed_impl`; firma e comportamento invariati."""
    return _write_text_if_changed_impl(path, text, tag=tag, keep=keep, skip_if_committed=skip_if_committed)
