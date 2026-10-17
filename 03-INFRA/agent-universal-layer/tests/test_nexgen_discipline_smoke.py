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
