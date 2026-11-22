"""Which interpreter runs the project's own pytest: the one that has its dependencies.

One job: name that interpreter. The engine runs from its own runtime, which
carries the engine's dependencies and not the project's, and a collection there
answered `No module named pytest` (2026-11-22), so the collected-tests gate could
not count a head at all. A venv belongs to a checkout, not to a repository, and
the copy a unit works in never has one: the owner's does. The interpreter is
therefore looked for in the repository's own checkouts, the main one first, so
nobody has to write a venv path by hand.

The copy is never a candidate, itself nor anything under it: an executor does not
provide the interpreter that judges it, or it could answer its own count.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from nexgen_core.paths import is_inside
from nexgen_core.processes import no_window_kwargs

#: Where a checkout keeps its own venv interpreter, in the order a checkout has one.
VENV_INTERPRETERS = (("bin", "python"), ("Scripts", "python.exe"), ("bin", "python3"))
#: The budget for asking git which checkouts the repository has: local bookkeeping, not a suite.
CHECKOUT_TIMEOUT_S = 30.0
#: The porcelain line that names one checkout; the prefix stripped to read its path.
WORKTREE_LINE = "worktree "


def project_python(repo: Path) -> str:
    """The interpreter that has the project's own dependencies: its venv's, else the engine's own.

    The fallback is the interpreter running the engine, which is what every
    collection used before a venv was consulted: a project that has no venv keeps
    the behaviour it had, and misses its own dependencies rather than borrowing
    them from somewhere else.
    """
    return venv_python(repo) or sys.executable


def venv_python(repo: Path) -> str:
    """The `.venv` interpreter of one of the repository's own checkouts, or "" when none has one.

    Git lists every checkout of the repository, the main one first, so the venv of
    the checkout the unit branched from is found without its path being written
    down. A machine that keeps the working checkout as a linked worktree beside
    the installed engine finds the venv there too, because it is a checkout of the
    same repository.

    The copy is skipped, itself and anything under it: an executor does not
    provide the interpreter that judges it, or it could answer its own count.
    """
    root = Path(repo).resolve()
    for checkout in _checkouts(root):
        if is_inside(checkout, root):
            continue
        for parts in VENV_INTERPRETERS:
            candidate = checkout.joinpath(".venv", *parts)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return ""


def _checkouts(root: Path) -> list[Path]:
    """Every checkout of `root`'s repository in git's own order, or none when `root` is not one."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=CHECKOUT_TIMEOUT_S, check=False, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):  # nexgen: allow reason="a git that cannot answer leaves no checkout to consult, so the fallback is used"
        return []
    if done.returncode:
        return []
    return [
        Path(line[len(WORKTREE_LINE):])
        for line in (done.stdout or "").splitlines() if line.startswith(WORKTREE_LINE)
    ]
