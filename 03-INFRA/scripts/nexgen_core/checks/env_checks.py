"""Controlli di ambiente, percorsi e directory di stato."""
from __future__ import annotations

from pathlib import Path

from nexgen_core.report import CheckOutcome, Severity


def check_state_dir(state_dir: Path) -> CheckOutcome:
    """Verifica e crea se necessario la directory di stato ~/.nexgen-engine."""
    if not state_dir.is_dir():
        def remedy() -> bool:
            state_dir.mkdir(parents=True, exist_ok=True)
            return True

        return CheckOutcome(
            id="env.state_dir",
            severity=Severity.BROKEN,
            message=f"La cartella di stato '{state_dir}' non esiste.",
            action=f"Crea la cartella con: mkdir -p {state_dir}",
            remedy=remedy,
        )
    return CheckOutcome(
        id="env.state_dir",
        severity=Severity.OK,
        message=f"Cartella di stato presente ({state_dir})",
    )


def check_vault_path(vault_path: Path) -> CheckOutcome:
    """Verifica che il percorso del Vault esista."""
    if not vault_path.is_dir():
        return CheckOutcome(
            id="env.vault_path",
            severity=Severity.BROKEN,
            message=f"La cartella del Knowledge Vault '{vault_path}' non è stata trovata.",
            action="Verifica la variabile d'ambiente KNOWLEDGE_VAULT_PATH o clona il Vault.",
        )
    return CheckOutcome(
        id="env.vault_path",
        severity=Severity.OK,
        message=f"Knowledge Vault trovato in {vault_path}",
    )
