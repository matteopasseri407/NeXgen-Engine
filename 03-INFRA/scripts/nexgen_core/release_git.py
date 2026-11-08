"""The git and gh calls `release ship` makes from outside: running them, reading their answers, pushing a stranded commit.

One job: the process plumbing under `release_ship.py`, kept apart so that file
stays under its line cap. Nothing here decides what a release does; the steps
and their order stay in `release_ship.py`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

Run = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def default_run(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # Resolved through PATH(EXT) so `gh.exe` and `nexgen.cmd` work on Windows too.
    exe = shutil.which(argv[0]) or argv[0]
    return subprocess.run(
        [exe, *argv[1:]], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=1800,
    )


def answer(text: str) -> dict:
    """GitHub's answer as a mapping, `{}` when it cannot be read.

    An unreadable answer leads to the same place as an empty one: the caller's
    own "GitHub will not say" path, which falls back to the strict reading.
    """
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:  # nexgen: allow reason="an unreadable answer is treated as no answer by the caller"
        return {}


def push_if_ahead(run: Run, repo: Path, branch: str) -> str:
    """Pushes `branch` when this checkout has commits origin does not have. Returns the reason it failed, or ''.

    A run that bumped VERSION and committed, then stopped before its push, leaves
    exactly this state. The version is already ahead, so a rerun does not bump
    again, and the release commit exists only on this machine: GitHub has never
    seen it, so the pull request that follows would name a commit it does not know.
    """
    head = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    remote = run(["git", "rev-parse", f"origin/{branch}"], repo).stdout.strip()
    if not head or head == remote:
        return ""
    pushed = run(["git", "push", "-q", "origin", branch], repo)
    if pushed.returncode == 0:
        return ""
    detail = (pushed.stderr or pushed.stdout).strip().splitlines()
    return f"pushing the release commit to {branch} failed: {detail[-1] if detail else pushed.returncode}"
