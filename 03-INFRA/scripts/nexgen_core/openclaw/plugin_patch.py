"""Exact text edits to a file of an installed OpenClaw plugin, as a bridge until upstream ships them.

One job: apply a fixed list of replacements to one file, only when the file
still has the shape this engine knows, beside a backup, and say what happened.

A plugin release overwrites these files, so every edit is idempotent and
recognisable: a run on a file that already carries the result writes nothing,
and a file this engine no longer recognises is named, never guessed at.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nexgen_core.files import atomic_write_text, backup_file

#: Every way a patch run can end, named once so the setup and its tests agree.
MISSING = "missing"
UNEXPECTED = "unexpected"
ALREADY = "already"
PATCHED = "patched"
WOULD_PATCH = "would-patch"
FAILED = "failed"


@dataclass(frozen=True)
class Patch:
    """What one run found and did, with the paths a person may need afterwards."""

    state: str
    path: Path
    backup: Path | None = None
    detail: str = ""


@dataclass(frozen=True)
class Edit:
    """One replacement: `old` must occur exactly once, unless `new` is already in the file."""

    old: str
    new: str


def apply(path: Path, edits: list[Edit], *, marker: str, backup_tag: str, dry_run: bool = False) -> Patch:
    """Bring `path` to the state the edits describe, and say what happened.

    `marker` is text only the finished patch contains: when it is there, the
    file is done. Otherwise each edit is either already in place (its `new` text
    is present, as after an earlier, smaller version of the patch) or must find
    its `old` text exactly once. Anything else is `unexpected`, which the setup
    prints, because a silent skip is how a fix quietly stops applying.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # nexgen: allow reason="an absent or unreadable plugin file is reported as missing, not raised"
        return Patch(MISSING, path, detail=str(exc))
    if marker in text:
        return Patch(ALREADY, path)
    pending = [edit for edit in edits if edit.new not in text]
    if not pending or any(text.count(edit.old) != 1 for edit in pending):
        return Patch(UNEXPECTED, path)
    if dry_run:
        return Patch(WOULD_PATCH, path)
    for edit in pending:
        text = text.replace(edit.old, edit.new, 1)
    try:
        backup = backup_file(path, tag=backup_tag)
        atomic_write_text(path, text)
    except OSError as exc:  # nexgen: allow reason="a plugin file that cannot be written is reported, never a crash after a successful install"
        return Patch(FAILED, path, detail=str(exc))
    return Patch(PATCHED, path, backup=backup)
