"""Unit test per Fase 4: doctor.py e checks/."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.doctor import Doctor


def test_doctor_diagnostics_on_empty(tmp_path: Path):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    state_dir = tmp_path / "state"

    doc = Doctor(vault_data=vault, state_dir=state_dir, home=home)
    report = doc.run_diagnostics(apply_remedies=True)

    # La cartella di stato viene creata dal rimedio automatico
    assert state_dir.is_dir()
    # Il vault non esiste quindi c'è un broken segnalato
    assert len(report.broken) >= 1
    assert any(b.id == "env.vault_path" for b in report.broken)


def test_doctor_diagnostics_on_healthy(tmp_path: Path):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    vault.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Struttura valida
    (vault / "03-INFRA" / "agent-universal-layer" / "mcp").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml").write_text("schema_version: 1\nservers: {}\n", encoding="utf-8")
    (vault / "03-INFRA" / "agent-universal-layer" / "skills").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").write_text("schema_version: 1\nskills: {}\n", encoding="utf-8")
    (vault / "03-INFRA" / "agent-universal-layer" / "instructions").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md").write_text("# Agents Rules\n", encoding="utf-8")

    doc = Doctor(vault_data=vault, state_dir=state_dir, home=home)
    report = doc.run_diagnostics(apply_remedies=True)

    assert report.ok_count >= 3
    # Verifichiamo che i rimedi abbiano sistemato mcp e index
    assert (home / ".agents" / "skills" / "INDEX.md").exists()
