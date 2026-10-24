"""Il tetto della verifica indipendente: mezz'ora, e il motore che può alzarlo dall'ambiente.

La suite di questo repository, su una macchina che altre unità stanno usando,
impiega fra i sei e i quindici minuti: dieci minuti di tetto l'hanno fatta andare
in timeout mentre passava (2026-10-24, unità `ritiro-cockpit`).
"""
from __future__ import annotations

import sys

import pytest

from nexgen_local import supervision_verify as verify


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
