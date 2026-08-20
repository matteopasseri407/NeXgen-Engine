"""Il piccolo wrapper git condiviso da gate.py e audit.py.

Riusa `nexgen_core.git_ops.run_git` (timeout, niente eccezioni sollevate da
subprocess stesso) e aggiunge la sola cosa che qui serve in più: un comando
git che fallisce durante il gate o l'audit non è qualcosa da ispezionare a
valle, è un guasto che deve fermare subito il chiamante.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.git_ops import run_git

DEFAULT_TIMEOUT_SECONDS = 30


class GitCommandError(RuntimeError):
    """Un comando git in `git(...)` è tornato con returncode != 0."""


def git(repo: Path | str, *args: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Esegue `git -C repo <args>`, ritorna stdout, solleva su errore."""
    result = run_git(Path(repo), *args, timeout=timeout)
    if result.returncode != 0:
        cmd = " ".join(("git", *args))
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise GitCommandError(f"`{cmd}` in {repo} failed: {detail}")
    return result.stdout
