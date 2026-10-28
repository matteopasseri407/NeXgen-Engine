"""Plain-Italian health report and the real-flow smoke command. Read-only, never blocks a commit."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path

from .config import Config, load
from .gitutil import Change, files_at, git
from .shellgate import check_shell
from .sizes import split_queue
from .violations import CODE_EXT, SHELL_EXT, Violation

WINDOW_DAYS = 7
GROWTH_RED = 5
QUEUE_SHOWN = 10


def findings(files: dict[str, str], cfg: Config) -> list[Violation]:
    """What the report shows at one revision: the split queue and the shell debt."""
    changes = [Change(path, "A", None, text) for path, text in files.items()]
    return split_queue(changes, cfg) + check_shell(changes, cfg)


def counts(found: list[Violation]) -> dict[str, int]:
    """The report's numbers, counted by kind."""
    return {kind: sum(v.kind == kind for v in found) for kind in ("file", "function", "shell")}


def _trend(now: int, before: int | None) -> str:
    return f"{now}" if before is None else f"{now} (una settimana fa: {before})"


def _status(smoke: str, now: dict[str, int], before: dict[str, int] | None) -> tuple[str, str, str]:
    grew = sum(now.values()) - sum(before.values()) if before else 0
    if not smoke:
        return "ROSSO", "manca il test del percorso reale", "Definisci smoke_command in .nexgen-discipline.toml: il comando che prova il programma davvero."
    if grew > GROWTH_RED:
        return "GIALLO", f"la coda split è cresciuta di {grew} voci in una settimana", "Niente blocca: guarda la coda split qui sotto e decidi cosa dividere quando vuoi."
    if grew > 0:
        return "GIALLO", f"la coda split è cresciuta di {grew} in una settimana", "Nessuna azione obbligata: il tetto è un avviso."
    if before is None:
        return "GIALLO", "progetto nuovo: ancora nessun dato di una settimana fa per confrontare", "Nessuna per ora."
    return "VERDE", "nessun peggioramento in una settimana", "Nessuna."


def _queue_section(found: list[Violation]) -> list[str]:
    """The split queue in the report: the work that is owed, up to a readable length."""
    queued = [v for v in found if v.kind in ("file", "function")]
    lines = [f"- Coda split ({len(queued)} voci, nessuna blocca il push):"]
    if not queued:
        return [*lines, "  - vuota"]
    lines += [f"  - {v}" for v in queued[:QUEUE_SHOWN]]
    if len(queued) > QUEUE_SHOWN:
        lines.append(f"  - ... e altre {len(queued) - QUEUE_SHOWN} voci: `discipline.py audit` le elenca tutte.")
    return lines


def report(root: Path) -> str:
    cfg = load(root, "HEAD")
    suffixes = CODE_EXT + SHELL_EXT
    found = findings(files_at(root, "HEAD", suffixes), cfg)
    now = counts(found)
    old_rev = git(root, "rev-list", "-1", f"--before={WINDOW_DAYS} days ago", "HEAD", check=False).strip()
    before = counts(findings(files_at(root, old_rev, suffixes), cfg)) if old_rev else None
    commits = git(root, "rev-list", "--count", f"--since={WINDOW_DAYS} days ago", "HEAD", check=False).strip() or "0"
    label, why, action = _status(cfg.smoke_command, now, before)
    smoke = f"definito (`{cfg.smoke_command}`)" if cfg.smoke_command else "NON DEFINITO"
    return "\n".join([
        f"RAPPORTO DI SALUTE: {root.name}, {date.today().isoformat()}",
        f"Stato: {label}, {why}.",
        f"- File oltre {cfg.max_file_lines} righe: {_trend(now['file'], before and before['file'])}",
        f"- Funzioni oltre {cfg.max_func_lines} righe: {_trend(now['function'], before and before['function'])}",
        f"- Script shell/PowerShell con logica: {_trend(now['shell'], before and before['shell'])}",
        *_queue_section(found),
        f"- Test del percorso reale: {smoke}",
        f"- Commit negli ultimi {WINDOW_DAYS} giorni: {commits}",
        f"Prossima azione: {action}",
    ])


def _with_this_interpreter(command: str) -> str:
    """`command` with a leading `python`/`python3` replaced by the interpreter running the engine.

    `python` is not a command on a stock Linux or macOS (only `python3` is), so the
    smoke failed with exit 127 from the pre-push hook before it ran anything. The
    replacement keeps whatever follows untouched, so `npm test` and `a && b` are
    still handed to the shell; Windows gets its own quoting because the interpreter
    usually lives under "Program Files".
    """
    head, _, rest = command.partition(" ")
    if Path(head).stem.lower() not in ("python", "python3"):
        return command
    interpreter = f'"{sys.executable}"' if os.name == "nt" else shlex.quote(sys.executable)
    return f"{interpreter} {rest}" if rest else interpreter


def run_smoke(root: Path) -> int:
    """Run the project's real-flow command. Not defined means a notice, never a failure."""
    command = _with_this_interpreter(load(root, "HEAD").smoke_command)
    if not command:
        print("smoke: nessun test del percorso reale definito (smoke_command in .nexgen-discipline.toml)")
        return 0
    try:  # the owner's own command, run as typed so `npm test` and `a && b` work on every OS
        return subprocess.run(command, cwd=root, shell=True, check=False).returncode  # noqa: S602
    except OSError as err:
        print(f"smoke: il comando non è partito: {err}")
        return 1
