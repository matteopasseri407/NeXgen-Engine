"""Plain-Italian health report and the real-flow smoke command. Read-only, never blocks a commit."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from .config import Config, load
from .gitutil import Change, files_at, git
from .shellgate import check_shell
from .sizes import check_sizes
from .violations import CODE_EXT, SHELL_EXT

WINDOW_DAYS = 7
GROWTH_RED = 5


def debt(files: dict[str, str], cfg: Config) -> dict[str, int]:
    """Existing size and shell debt, counted by kind."""
    changes = [Change(p, "A", None, text) for p, text in files.items()]
    found = check_sizes(changes, cfg) + check_shell(changes, cfg)
    return {kind: sum(v.kind == kind for v in found) for kind in ("file", "function", "shell")}


def _trend(now: int, before: int | None) -> str:
    return f"{now}" if before is None else f"{now} (una settimana fa: {before})"


def _status(smoke: str, now: dict[str, int], before: dict[str, int] | None) -> tuple[str, str, str]:
    grew = sum(now.values()) - sum(before.values()) if before else 0
    if not smoke:
        return "ROSSO", "manca il test del percorso reale", "Definisci smoke_command in .nexgen-discipline.toml: il comando che prova il programma davvero."
    if grew > GROWTH_RED:
        return "ROSSO", f"il debito è cresciuto di {grew} voci in una settimana", "Fermati: nessun codice nuovo nei file elencati da `audit` finché non rientra."
    if grew > 0:
        return "GIALLO", f"il debito è cresciuto di {grew} in una settimana", "Controlla `audit`: chi ha ingrandito file o funzioni oltre i limiti?"
    if before is None:
        return "GIALLO", "progetto nuovo: ancora nessun dato di una settimana fa per confrontare", "Nessuna per ora."
    return "VERDE", "nessun peggioramento in una settimana", "Nessuna."


def report(root: Path) -> str:
    cfg = load(root, "HEAD")
    suffixes = CODE_EXT + SHELL_EXT
    now = debt(files_at(root, "HEAD", suffixes), cfg)
    old_rev = git(root, "rev-list", "-1", f"--before={WINDOW_DAYS} days ago", "HEAD", check=False).strip()
    before = debt(files_at(root, old_rev, suffixes), cfg) if old_rev else None
    commits = git(root, "rev-list", "--count", f"--since={WINDOW_DAYS} days ago", "HEAD", check=False).strip() or "0"
    label, why, action = _status(cfg.smoke_command, now, before)
    smoke = f"definito (`{cfg.smoke_command}`)" if cfg.smoke_command else "NON DEFINITO"
    return "\n".join([
        f"RAPPORTO DI SALUTE: {root.name}, {date.today().isoformat()}",
        f"Stato: {label}, {why}.",
        f"- File oltre {cfg.max_file_lines} righe: {_trend(now['file'], before and before['file'])}",
        f"- Funzioni oltre {cfg.max_func_lines} righe: {_trend(now['function'], before and before['function'])}",
        f"- Script shell/PowerShell con logica: {_trend(now['shell'], before and before['shell'])}",
        f"- Test del percorso reale: {smoke}",
        f"- Commit negli ultimi {WINDOW_DAYS} giorni: {commits}",
        f"Prossima azione: {action}",
    ])


def run_smoke(root: Path) -> int:
    """Run the project's real-flow command. Not defined means a notice, never a failure."""
    command = load(root, "HEAD").smoke_command
    if not command:
        print("smoke: nessun test del percorso reale definito (smoke_command in .nexgen-discipline.toml)")
        return 0
    try:  # the owner's own command, run as typed so `npm test` and `a && b` work on every OS
        return subprocess.run(command, cwd=root, shell=True, check=False).returncode  # noqa: S602
    except OSError as err:
        print(f"smoke: il comando non è partito: {err}")
        return 1
