"""The project's own smoke: a real script, not the gate, and under its budget.

The pre-push hook runs it on every push, so a smoke that re-runs the gate costs
exactly the minutes this project just decided not to spend: `smoke_command` has
to name a real, quick, read-only proof that the engine starts on this machine.
"""
from __future__ import annotations

import time
from pathlib import Path

from nexgen_core.discipline.config import load
from nexgen_core.discipline.health import run_smoke

REPO = Path(__file__).resolve().parents[3]
SMOKE = REPO / "03-INFRA" / "scripts" / "smoke.py"
BUDGET_SECONDS = 30


def test_the_project_smoke_is_a_real_script_and_not_the_gate():
    command = load(REPO, "HEAD").smoke_command
    assert "smoke.py" in command
    assert "discipline.py" not in command
    assert SMOKE.is_file()


def test_the_smoke_proves_the_checkout_on_this_machine_under_its_budget():
    started = time.monotonic()
    assert run_smoke(REPO) == 0
    assert time.monotonic() - started < BUDGET_SECONDS


def test_a_replacement_character_on_a_cp1252_stdout_does_not_crash_the_smoke(monkeypatch):
    import importlib.util
    import io
    import sys

    spec = importlib.util.spec_from_file_location("smoke_script", SMOKE)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(smoke, "check_commands", lambda: [] if smoke._report("nexgen plan", True, "Raggiungibilit\ufffd") else ["x"])
    monkeypatch.setattr(smoke, "check_launcher", lambda: [])
    assert smoke.main() == 0


def test_a_failing_launcher_shows_its_own_error(monkeypatch, capsys):
    import importlib.util
    import subprocess

    from nexgen_core import shims

    spec = importlib.util.spec_from_file_location("smoke_script_launcher", SMOKE)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    monkeypatch.setattr(shims, "install_shims", lambda **_: [str(REPO / "nexgen.cmd")])
    stderr = (
        "DIAG-LINE-1\nline two\nline three\nline four\nline five\nline six\n"
        "ModuleNotFoundError: No module named 'yaml'\n"
    )
    monkeypatch.setattr(smoke, "run", lambda argv: subprocess.CompletedProcess(argv, 1, "", stderr))
    problems = smoke.check_launcher()
    printed = capsys.readouterr().out
    assert "ModuleNotFoundError: No module named 'yaml'" in printed
    assert "DIAG-LINE-1" not in printed
    assert any("ModuleNotFoundError: No module named 'yaml'" in problem for problem in problems)
