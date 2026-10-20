"""The git configuration of a unit's copy: its fingerprint at creation, and what changed since.

One job: make a change to the copy's git configuration visible. `git config` run
from a worktree without `--worktree` writes the owner's shared `.git/config`,
which every checkout of the repository reads, and a `url.*.insteadOf` rule
re-points a push while every URL the copy reports stays exactly as it was
written down. The unit may not edit either file and call it nothing.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from nexgen_core.discipline.gitutil import git

#: The shared configuration file's name inside the common git directory.
SHARED_FILE = "config"
#: The last components of the keys that rewrite a URL for fetch or for push.
OVERRIDE_SUFFIXES = (".insteadof", ".pushinsteadof")


def paths(root: Path) -> dict[str, Path]:
    """The two configuration files of this copy: the repository's shared one and the worktree's own."""
    shared = _absolute(root, git(root, "rev-parse", "--git-common-dir").strip())
    own = _absolute(root, git(root, "rev-parse", "--git-path", "config.worktree").strip())
    return {"shared": shared / SHARED_FILE, "worktree": own}


def fingerprint(root: Path) -> dict[str, str]:
    """Each configuration file hashed as it is now, keyed by its path; a missing file is an empty hash."""
    return {Path(path).as_posix(): _file_hash(path) for path in paths(root).values()}


def url_override_keys(root: Path) -> list[str]:
    """Every `url.*.insteadOf` / `url.*.pushInsteadOf` key the effective configuration carries now.

    Only the keys, never the values: a rewrite rule is what re-points a push, and a
    value can carry a URL with a token in it. The effective configuration is the
    one git will use, so a rule added to the owner's global file is seen too.
    """
    lines = git(root, "config", "--get-regexp", r"^url\.", check=False).splitlines()
    keys = {line.split(" ", 1)[0] for line in lines if line.strip()}
    return sorted(key for key in keys if key.endswith(OVERRIDE_SUFFIXES))


def check(folder: Path, record: dict) -> tuple[bool, str]:
    """Whether the copy's configuration and its URL rules are still the creation's, naming what changed."""
    saved = {str(path): str(value) for path, value in (record.get("config_hashes") or {}).items()}
    if not saved:
        return True, "unità senza impronta della configurazione nel diario"
    now = fingerprint(folder)
    problems = []
    changed = sorted(path for path, value in saved.items() if now.get(path, "") != value)
    if changed:
        problems.append("configurazione git cambiata dopo la creazione: " + ", ".join(changed))
    added = sorted(set(url_override_keys(folder)) - {str(key) for key in record.get("url_override_keys") or []})
    if added:
        problems.append("regole url.*.insteadOf aggiunte dopo la creazione: " + ", ".join(added))
    if problems:
        return False, " | ".join(problems)
    return True, "configurazione git e regole url.* come alla creazione"


def _absolute(root: Path, printed: str) -> Path:
    """A path git printed, which is relative to the copy when git chose to print it that way."""
    path = Path(printed)
    return path if path.is_absolute() else root / path


def _file_hash(path: Path) -> str:
    """The SHA-256 of a file's bytes, or an empty string when the file does not exist."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:  # nexgen: allow reason="a config file that is missing has no hash, and the comparison reports it"
        return ""
