"""Il tetto della verifica indipendente, e l'interprete che un `python` dichiarato significa.

La suite di questo repository, su una macchina che altre unità stanno usando,
impiega fra i sei e i quindici minuti: dieci minuti di tetto l'hanno fatta andare
in timeout mentre passava (2026-10-24, unità `ritiro-cockpit`).

Il 2026-11-22 la verifica dichiarata `python -m pytest ...` è partita con il
Python dello Store che PATH offre per primo, e i test del progetto sono caduti su
`No module named 'filelock'`: un `python` nudo non è l'interprete del progetto.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from nexgen_local import supervision_verify as verify

from supervision_repo import build, git_in


def test_the_default_budget_is_half_an_hour(monkeypatch):
    monkeypatch.delenv(verify.TIMEOUT_ENV, raising=False)

    assert verify.TIMEOUT_S == 1800.0
    assert verify.budget() == verify.TIMEOUT_S


def test_the_engine_environment_moves_the_budget(monkeypatch):
    """Una macchina più lenta del default alza il tetto senza toccare il codice."""
    monkeypatch.setenv(verify.TIMEOUT_ENV, "2400")

    assert verify.budget() == 2400.0


@pytest.mark.parametrize("value", ["", "   ", "tanto", "0", "-5"])
def test_a_value_that_is_not_a_positive_number_leaves_the_ceiling_alone(monkeypatch, value):
    """Un refuso nell'ambiente non toglie il tetto che ferma un comando appeso."""
    monkeypatch.setenv(verify.TIMEOUT_ENV, value)

    assert verify.budget() == verify.TIMEOUT_S


def test_the_run_uses_the_budget_it_announces(tmp_path, monkeypatch):
    """Il numero che il comando riceve è lo stesso che il motivo nomina in caso di timeout."""
    monkeypatch.setenv(verify.TIMEOUT_ENV, "1234")
    seen: dict = {}
    real = verify.subprocess.run

    def spy(argv, **kwargs):
        seen.update(kwargs)
        return real(argv, **kwargs)

    monkeypatch.setattr(verify.subprocess, "run", spy)

    ok, reason, _ = verify.run(tmp_path, f'"{sys.executable}" -c "print(1)"')

    assert ok, reason
    assert seen["timeout"] == 1234.0


# --- un `python` nudo è l'interprete del progetto, non quello che PATH offre ---


def _owner_venv(checkout: Path) -> Path:
    """A checkout's own `.venv` interpreter: the file the rule looks for, not a Python that runs."""
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    interpreter = checkout / ".venv" / relative
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_text("", encoding="utf-8")
    interpreter.chmod(0o755)
    return interpreter


def _worktree(repo, folder: Path, branch: str) -> Path:
    """A linked worktree of `repo`: the shape of the unit's copy, and of the owner's checkout."""
    git_in(repo.path, "worktree", "add", "-q", "-b", branch, str(folder))
    return folder


def _launched_pytest(monkeypatch, output: str = "1 test collected in 0.01s\n") -> list[list[str]]:
    """Every pytest launch the module makes, answered without running it."""
    seen: list[list[str]] = []
    real = verify.subprocess.run

    def spy(argv, **kwargs):
        if isinstance(argv, list) and "pytest" in argv:
            seen.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
        return real(argv, **kwargs)

    monkeypatch.setattr(verify.subprocess, "run", spy)
    return seen


def test_a_bare_python_is_the_owners_venv_not_what_path_offers(tmp_path, monkeypatch):
    """Il `python` dichiarato gira nel venv del proprietario: PATH offriva il Python dello Store."""
    repo = build(tmp_path / "sandbox")
    copy = _worktree(repo, tmp_path / "copia", "unita")
    interpreter = _owner_venv(repo.path)
    seen = _launched_pytest(monkeypatch)

    ok, reason, _ = verify.run(copy, "python -m pytest -q")

    assert ok, reason
    assert seen == [[str(interpreter), "-P", "-m", "pytest", "-q"]]


def test_the_verification_never_borrows_the_copys_python(tmp_path, monkeypatch):
    """La regola di sempre vale anche per l'interprete: la copia non fornisce chi la verifica."""
    repo = build(tmp_path / "sandbox")
    copy = _worktree(repo, tmp_path / "copia", "unita")
    _owner_venv(copy)
    seen = _launched_pytest(monkeypatch)

    ok, reason, _ = verify.run(copy, "python -m pytest -q")

    assert ok, reason
    assert seen == [["python", "-P", "-m", "pytest", "-q"]], "resta il nome dichiarato, mai la copia"


def test_an_explicit_interpreter_path_is_the_owners_choice(tmp_path, monkeypatch):
    """Chi scrive un percorso assoluto ha già scelto: il motore non lo sostituisce."""
    repo = build(tmp_path / "sandbox")
    copy = _worktree(repo, tmp_path / "copia", "unita")
    _owner_venv(repo.path)
    chosen = str(tmp_path / "altrove" / "python.exe")
    seen = _launched_pytest(monkeypatch)

    ok, reason, _ = verify.run(copy, f'"{chosen}" -m pytest -q')

    assert ok, reason
    assert seen == [[chosen, "-P", "-m", "pytest", "-q"]]
