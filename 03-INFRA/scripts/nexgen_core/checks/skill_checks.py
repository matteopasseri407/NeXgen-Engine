"""Controlli di coerenza del catalogo e delle viste delle skill."""
from __future__ import annotations

from pathlib import Path

from nexgen_core.config import load_skills_manifest
from nexgen_core.report import CheckOutcome, Severity
from nexgen_core.skills import SkillMaterializer


def check_skills_manifest(manifest_path: Path) -> CheckOutcome:
    """Verifica che skills.manifest.yaml sia presente e valido."""
    if not manifest_path.is_file():
        return CheckOutcome(
            id="skills.manifest_present",
            severity=Severity.BROKEN,
            message=f"Il manifest delle skill '{manifest_path}' non esiste.",
            action="Crea o ripristina skills.manifest.yaml dai template.",
        )

    try:
        data = load_skills_manifest(manifest_path)
        skill_count = len(data.get("skills", {}))
        return CheckOutcome(
            id="skills.manifest_valid",
            severity=Severity.OK,
            message=f"Manifest skill valido con {skill_count} skill dichiarate",
        )
    except Exception as exc:
        return CheckOutcome(
            id="skills.manifest_valid",
            severity=Severity.BROKEN,
            message=f"Il manifest delle skill contiene errori: {exc}",
            action="Correggi la sintassi di skills.manifest.yaml.",
        )


def check_skill_library_and_index(vault_data: Path, home: Path) -> CheckOutcome:
    """Verifica che la libreria di skill e l'indice INDEX.md esistano."""
    index_file = home / ".agents" / "skills" / "INDEX.md"
    mat = SkillMaterializer(vault_data=vault_data, home=home)

    def remedy() -> bool:
        mat.materialize(apply=True)
        return True

    if not index_file.is_file():
        return CheckOutcome(
            id="skills.index_present",
            severity=Severity.BROKEN,
            message="Il catalogo delle skill (~/.agents/skills/INDEX.md) non è presente.",
            action="Esegui 'agent-sync apply' per materializzare le skill e rigenerare l'indice.",
            remedy=remedy,
        )

    return CheckOutcome(
        id="skills.index_present",
        severity=Severity.OK,
        message="Catalogo delle skill presente e allineato",
    )
