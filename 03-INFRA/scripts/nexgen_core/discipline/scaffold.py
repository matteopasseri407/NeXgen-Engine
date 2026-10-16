"""`init`: make any project follow the discipline: config, decision log and local git hooks."""
from __future__ import annotations

import json
import shlex
import stat
import sys
from pathlib import Path

from ..files import atomic_write_text
from .config import CONFIG_NAME
from .gitutil import git

MANAGED = "# managed by nexgen discipline init"
ENGINE = Path(__file__).resolve().parents[2] / "discipline.py"
HOOK = """#!/bin/sh
{managed}
PY={python}
ENGINE={engine}
if [ ! -x "$PY" ] || [ ! -f "$ENGINE" ]; then
  echo "discipline: python ($PY) or engine ($ENGINE) not found; run 'discipline.py init' again from a current NeXgen install" >&2
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

- AAAA-MM-GG: (cosa hai deciso) perché (il motivo in una frase).
"""


def _config_text(smoke: str) -> str:
    return ("# Limiti e opzioni della disciplina di questo progetto (valgono i valori già committati).\n"
            f"smoke_command = {json.dumps(smoke)}  # il comando che prova il programma davvero\n"
            "# review_paths = []  # percorsi di produzione che richiedono una revisione se la modifica è complessa\n")


def _install_hook(hooks: Path, name: str) -> tuple[str, bool]:
    path = hooks / name
    if path.exists() and MANAGED not in path.read_text(encoding="utf-8", errors="replace"):
        return f"hook {name}: ne esiste già uno non nostro, NON installato (integralo a mano)", False
    text = HOOK.format(managed=MANAGED, python=shlex.quote(Path(sys.executable).as_posix()),
                       engine=shlex.quote(ENGINE.as_posix()), commands=HOOK_COMMANDS[name])
    atomic_write_text(path, text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return f"hook {name}: installato", True


def init_project(root: Path, smoke: str = "") -> tuple[list[str], bool]:
    """Create what is missing; never overwrite the owner's files. Returns (what it did, whether all is protected)."""
    done: list[str] = []
    for name, text in ((CONFIG_NAME, _config_text(smoke)), ("DECISIONS.md", DECISIONS)):
        if not (root / name).exists():
            atomic_write_text(root / name, text)
            done.append(f"creato {name}")
    if git(root, "config", "--get", "core.hooksPath", check=False).strip():
        done.append("hook NON installati: core.hooksPath è impostato, scriverli lì li metterebbe su altri progetti. "
                    "Toglilo o integra a mano.")
        return done, False
    hooks = Path(git(root, "rev-parse", "--git-path", "hooks").strip())
    hooks = hooks if hooks.is_absolute() else root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    results = [_install_hook(hooks, name) for name in HOOK_COMMANDS]
    done += [line for line, _ in results]
    if not smoke:
        done.append("ATTENZIONE: manca smoke_command, il rapporto resta ROSSO finché non lo definisci")
    return done, all(ok for _, ok in results)
