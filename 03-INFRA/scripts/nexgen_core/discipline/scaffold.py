"""`init`: make any project follow the discipline: config, decision log and local git hooks."""
from __future__ import annotations

import stat
from pathlib import Path

from ..files import atomic_write_text
from .config import CONFIG_NAME
from .gitutil import git

MANAGED = "# managed by nexgen discipline init"
ENGINE = Path(__file__).resolve().parents[2] / "discipline.py"
HOOK = """#!/bin/sh
{managed}
ENGINE="{engine}"
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ] || [ ! -f "$ENGINE" ]; then
  echo "discipline: engine not found ($ENGINE) or no python; run 'discipline.py init' from a current NeXgen install" >&2
  exit 1
fi
{commands}
"""
HOOK_COMMANDS = {
    "pre-commit": '"$PY" "$ENGINE" check --staged || exit 1',
    "pre-push": '"$PY" "$ENGINE" gate || exit 1\n"$PY" "$ENGINE" smoke || exit 1',
}
DECISIONS = """# Decisioni del proprietario

Una riga per decisione: data, cosa, perché. Gli agent la leggono e non la rimettono in discussione.

- (esempio) 2026-10-16: la prossima versione è 2.16.0 e non 3.0.0, perché non rompe nulla.
"""


def _config_text(smoke: str) -> str:
    return (f"# Limiti e opzioni della disciplina di questo progetto (valgono i valori già committati).\n"
            f'smoke_command = "{smoke}"  # il comando che prova il programma davvero\n'
            "# review_paths = []  # percorsi di produzione che richiedono una revisione se la modifica è complessa\n")


def _install_hook(hooks: Path, name: str) -> str:
    path = hooks / name
    if path.exists() and MANAGED not in path.read_text(encoding="utf-8", errors="replace"):
        return f"hook {name}: esiste già un hook non nostro, non lo tocco"
    atomic_write_text(path, HOOK.format(managed=MANAGED, engine=ENGINE.as_posix(), commands=HOOK_COMMANDS[name]).replace("\\n", "\n"))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return f"hook {name}: installato"


def init_project(root: Path, smoke: str = "") -> list[str]:
    """Create what is missing; never overwrite the owner's files. Returns what it did."""
    done = []
    for name, text in ((CONFIG_NAME, _config_text(smoke)), ("DECISIONS.md", DECISIONS)):
        if not (root / name).exists():
            atomic_write_text(root / name, text)
            done.append(f"creato {name}")
    hooks = Path(git(root, "rev-parse", "--git-path", "hooks").strip())
    hooks = hooks if hooks.is_absolute() else root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    done += [_install_hook(hooks, name) for name in HOOK_COMMANDS]
    if not smoke:
        done.append("ATTENZIONE: manca smoke_command, il rapporto resta ROSSO finché non lo definisci")
    return done
