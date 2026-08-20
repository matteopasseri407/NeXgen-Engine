"""Unit test per nexgen_core/report.py (Fase 0: Il misuratore)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Assicura che il package nexgen_core sia importabile
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.report import CheckOutcome, Report, Severity


def test_check_outcome_creation():
    ok = CheckOutcome(id="test.ok", severity=Severity.OK, message="Tutto ok")
    assert ok.severity == Severity.OK
    assert ok.remedied is False
    assert ok.to_dict()["severity"] == "ok"

    broken = CheckOutcome(
        id="test.broken",
        severity="broken",
        message="File mancante",
        action="Esegui touch file.txt",
        detail="Percorso /tmp/file.txt non trovato"
    )
    assert broken.severity == Severity.BROKEN
    assert broken.action == "Esegui touch file.txt"


def test_report_remedy_execution():
    fixed = False

    def my_remedy():
        nonlocal fixed
        fixed = True
        return True

    report = Report()
    broken_with_remedy = CheckOutcome(
        id="test.auto_fix",
        severity=Severity.BROKEN,
        message="Cartella mancante",
        remedy=my_remedy
    )

    report.add(broken_with_remedy, apply_remedy=True)
    assert fixed is True
    assert broken_with_remedy.remedied is True
    assert broken_with_remedy.severity == Severity.OK
    assert report.ok_count == 1
    assert len(report.broken) == 0
    assert report.is_healthy is True


def test_report_broken_without_remedy():
    report = Report()
    broken = CheckOutcome(
        id="test.fail",
        severity=Severity.BROKEN,
        message="Credenziali mancanti",
        action="Imposta la variabile API_KEY"
    )
    report.add(broken)
    assert report.is_healthy is False
    assert report.has_failures is True
    assert report.exit_code() == 1
    assert len(report.broken) == 1

    human_out = report.format_human()
    # L'invariante è che il guasto e la sua azione compaiano, non con quale
    # frase il referto li introduce: quella è testo di prodotto e cambia.
    assert "Credenziali mancanti" in human_out
    assert "Imposta la variabile API_KEY" in human_out


def test_report_undetermined():
    report = Report()
    undet = CheckOutcome(
        id="test.network",
        severity=Severity.UNDETERMINED,
        message="Connessione internet non disponibile"
    )
    report.add(undet)
    assert report.is_healthy is False
    assert report.has_failures is False
    assert report.exit_code(strict=False) == 0
    assert report.exit_code(strict=True) == 1

    human_out = report.format_human()
    assert "Connessione internet non disponibile" in human_out, (
        "un esito indeterminato deve comparire nel referto, nominato"
    )


def test_report_json_serialization():
    report = Report()
    report.add(CheckOutcome(id="test.1", severity=Severity.OK, message="Ok 1"))
    report.add(CheckOutcome(id="test.2", severity=Severity.BROKEN, message="Rotto", action="Risolvi"))

    json_str = report.format_json()
    data = json.loads(json_str)
    assert data["ok_count"] == 1
    assert data["broken_count"] == 1
    assert len(data["broken"]) == 1
    assert data["broken"][0]["id"] == "test.2"
