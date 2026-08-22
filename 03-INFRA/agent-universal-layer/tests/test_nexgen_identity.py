"""Il confine dell'identità: due gravità diverse, e non vanno mai confuse.

Il 14 agosto una riga di frontmatter mancante ha fatto uscire con errore la
guardia dell'identità; systemd ha cancellato il sync che dipendeva da lei, e
per sei giorni nessuno se n'è accorto. Questi test fissano la regola nata da
lì: un difetto di forma si dice, una violazione di confine pesa.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks.identity_checks import (
    check_agent_self,
    check_agent_self_metadata,
    check_native_memory_boundary,
)
from nexgen_core.report import Severity


def _write_self(vault: Path, body: str) -> Path:
    target = vault / "03-INFRA" / "agent-universal-layer" / "agent-self.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def test_a_missing_personal_space_is_not_a_fault(tmp_path: Path):
    # Nessuno è obbligato ad averlo: assente non è rotto.
    assert check_agent_self(tmp_path).severity is Severity.UNDETERMINED


def test_a_well_formed_personal_space_is_silent(tmp_path: Path):
    _write_self(tmp_path, "---\nstatus: active\n---\n\nCorpo.\n")
    assert check_agent_self(tmp_path).severity is Severity.OK
    assert check_agent_self_metadata(tmp_path).severity is Severity.OK


def test_a_metadata_defect_is_reported_and_says_it_blocks_nothing(tmp_path: Path):
    _write_self(tmp_path, "Nessun frontmatter qui.\n")
    outcome = check_agent_self_metadata(tmp_path)
    assert outcome.severity is Severity.BROKEN
    assert outcome.action, "un difetto va sempre con l'azione che lo chiude"
    detail = (outcome.detail or "").lower()
    assert any(word in detail for word in ("non impedisce", "block", "stop")), (
        "il referto deve dire esplicitamente che un difetto di forma non ferma "
        "niente: è la lezione del 14 agosto"
    )


def test_a_missing_required_key_is_a_metadata_defect_not_a_boundary_one(tmp_path: Path):
    _write_self(tmp_path, "---\ntitle: qualcosa\n---\n\nCorpo.\n")
    outcome = check_agent_self_metadata(tmp_path)
    assert outcome.severity is Severity.BROKEN
    assert outcome.id == "identity.self_metadata"


def test_no_parallel_memory_is_silence(tmp_path: Path):
    assert check_native_memory_boundary(tmp_path).severity is Severity.OK


def test_an_empty_native_store_is_not_a_second_memory(tmp_path: Path):
    # Una cartella vuota che un runtime crea da solo non è una memoria.
    (tmp_path / ".claude" / "memory").mkdir(parents=True)
    assert check_native_memory_boundary(tmp_path).severity is Severity.OK


def test_a_populated_native_store_is_a_boundary_violation(tmp_path: Path):
    store = tmp_path / ".claude" / "memory"
    store.mkdir(parents=True)
    (store / "un-fatto.md").write_text("ricordato altrove", encoding="utf-8")

    outcome = check_native_memory_boundary(tmp_path)

    assert outcome.severity is Severity.BROKEN
    detail = (outcome.detail or "").lower()
    assert any(word in detail for word in ("cancellato", "delete", "remov")), (
        "il referto deve dire che non viene cancellato niente: la scelta resta "
        "dell'utente"
    )


def test_transcripts_are_not_treated_as_memory(tmp_path: Path):
    # Le trascrizioni di sessione sono materiale grezzo, non una seconda
    # verità: trattarle come tali riempirebbe il referto di falsi allarmi.
    for relative in (".codex/sessions", ".opencode/storage", ".gemini/tmp"):
        path = tmp_path / relative
        path.mkdir(parents=True)
        (path / "sessione.jsonl").write_text("{}", encoding="utf-8")

    assert check_native_memory_boundary(tmp_path).severity is Severity.OK


def test_status_uninitialized_is_valid(tmp_path: Path):
    _write_self(tmp_path, "---\nstatus: uninitialized\n---\n\nNessuna scelta ancora.\n")
    assert check_agent_self_metadata(tmp_path).severity is Severity.OK


def test_status_invalid_is_a_shape_defect_not_blocking(tmp_path: Path):
    _write_self(tmp_path, "---\nstatus: attivato-ora\n---\n\nCorpo.\n")
    outcome = check_agent_self_metadata(tmp_path)
    assert outcome.severity is Severity.WARN
    assert "uninitialized / active" in (outcome.action or "")


def test_example_template_is_well_formed(tmp_path: Path):
    example = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "templates" / "agent-self.md.example"
    assert example.is_file(), "il template sanificato deve essere spedito dal motore"
    _write_self(tmp_path, example.read_text(encoding="utf-8"))
    assert check_agent_self(tmp_path).severity is Severity.OK
    assert check_agent_self_metadata(tmp_path).severity is Severity.OK
