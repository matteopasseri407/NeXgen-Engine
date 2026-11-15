"""The set-aside copies of a skill view: what they are called, and how many survive.

One job: name the backup next to a live path, and prune the oldest ones so a
flapping skill view cannot pile one copy per guard cycle next to itself. It was
the bottom half of `skill_sources.py`; it moved here when that file reached its
size ceiling and the github fetch had to grow, and it is the same behavior under
the same tests.

Two shapes of name are pruned, because two writers make them: `<name>.bak-<stamp>`
from the materializer, and `<name>.pre-<tag>-<stamp>.bak` from `files.backup_file`.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

#: How many set-aside skill views survive rotation next to the live one.
SKILL_BACKUP_KEEP = 3


def next_backup_path(path: Path) -> Path:
    """A free backup path next to `path`."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    return candidate


def prune_skill_backups(path: Path) -> None:
    """Drops rotated-out set-aside views, oldest first."""
    seen: set[Path] = set()
    for pattern in (f"{path.name}.bak-*", f"{path.name}.pre-*.bak"):
        seen.update(path.parent.glob(pattern))
    ordered = sorted(seen)
    is_junction = getattr(os.path, "isjunction", lambda p: False)
    for old in ordered[: max(len(ordered) - SKILL_BACKUP_KEEP, 0)]:
        # unlink() drops files, symlinks AND junctions without touching
        # any target (rmtree would follow a junction into a live tree).
        try:
            if old.is_symlink() or old.is_file() or is_junction(old):
                old.unlink()
            else:
                shutil.rmtree(old)
        except OSError:  # nexgen: allow reason="a backup the system still holds open is not a sync failure: the next prune tries again"
            pass
