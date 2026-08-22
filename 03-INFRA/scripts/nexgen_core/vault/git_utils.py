"""The small git wrapper shared by gate.py and audit.py.

Reuses `nexgen_core.git_ops.run_git` (timeout, no exceptions raised by
subprocess itself) and adds the one extra thing needed here: a git command
that fails during the gate or the audit isn't something to inspect
downstream, it's a failure that must stop the caller immediately.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.git_ops import run_git

DEFAULT_TIMEOUT_SECONDS = 30


class GitCommandError(RuntimeError):
    """A git command in `git(...)` came back with returncode != 0."""


def git(repo: Path | str, *args: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Runs `git -C repo <args>`, returns stdout, raises on error."""
    result = run_git(Path(repo), *args, timeout=timeout)
    if result.returncode != 0:
        cmd = " ".join(("git", *args))
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise GitCommandError(f"`{cmd}` in {repo} failed: {detail}")
    return result.stdout
