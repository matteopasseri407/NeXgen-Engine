"""Ruff on a unit's copy, measured against the copy's own committed baseline.

One job: run the project's lint gate where the unit actually works and answer
three things — did it run, did it pass, and what did it say — so the collect
gates can report lint debt without owning the logic. The engine's script is the
one that runs when it exists, because a verifier the unit can edit is not a
verifier; `NEXGEN_RUFF_ENTRY_DIR` is what points that script at the copy instead
of at the engine. The copy decides only whether it has the gate at all and what
its baseline allows.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from nexgen_core.processes import no_window_kwargs

from . import supervision_pytest as pytest_runs

#: Where a project keeps the lint gate, and how long it may take.
RELATIVE = Path("03-INFRA") / "scripts" / "ruff_baseline_check.py"
TIMEOUT_S = 300.0
#: The line the baseline script prints when it finds nothing new.
OK_LINE = "nessuna regressione rispetto alla baseline della copia"


def run(folder: Path) -> tuple[bool, str, str]:
    """Whether this copy adds lint debt: the verdict, one line of reason, the raw output."""
    script = script_for(folder)
    if script is None:
        return True, (
            "il progetto non ha 03-INFRA/scripts/ruff_baseline_check.py: "
            "nessun debito lint da confrontare"
        ), ""
    env = pytest_runs.cleaned_environment()
    env["NEXGEN_RUFF_ENTRY_DIR"] = str((folder / RELATIVE.parent).resolve())
    try:
        done = subprocess.run(
            [sys.executable, str(script)], cwd=folder, capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=env,
            timeout=TIMEOUT_S, check=False, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return False, f"ruff non ha risposto entro {TIMEOUT_S:.0f}s", ""
    except OSError as exc:
        return False, f"ruff non avviato ({type(exc).__name__}: {exc})", ""
    output = (done.stdout + done.stderr).strip()
    if done.returncode:
        return False, f"regressione rispetto alla baseline: {_last_line(output)}", output
    return True, OK_LINE, output


def script_for(folder: Path) -> Path | None:
    """The baseline check to run on this copy, or None when the project has no such gate.

    The engine's own script wins over the copy's: a copy that carries a weakened
    version of the check must not be the one deciding whether it passes. An
    installed engine has no repository scripts beside it, and there the copy's
    own is the only one there is.
    """
    marker = folder / RELATIVE
    if not marker.is_file():
        return None
    own = Path(__file__).resolve().parents[1] / RELATIVE.name
    return own if own.is_file() else marker


def _last_line(text: str) -> str:
    """The line that names the regression, for a gate reason that fits on one row."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:200]
