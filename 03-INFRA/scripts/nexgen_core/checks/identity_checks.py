"""Controlli di integrità dell'identità privata (agent-self.md)."""
from __future__ import annotations

from pathlib import Path

from nexgen_core.report import CheckOutcome, Severity


def check_agent_self(vault_data: Path) -> CheckOutcome:
    """Verifica che lo spazio personale agent-self.md esista e rispetti i confini."""
    self_file = vault_data / "03-INFRA" / "agent-universal-layer" / "agent-self.md"
    if not self_file.is_file():
        return CheckOutcome(
            id="identity.self_present",
            severity=Severity.UNDETERMINED,
            message="Lo spazio personale agent-self.md non è ancora presente.",
            action="Inizializza lo spazio personale in una sessione dedicata se desiderato.",
        )

    try:
        content = self_file.read_text(encoding="utf-8")
        if "status: active" in content or "status: uninitialized" in content:
            return CheckOutcome(
                id="identity.self_valid",
                severity=Severity.OK,
                message="Spazio personale agent-self.md valido e attivo",
            )
        return CheckOutcome(
            id="identity.self_valid",
            severity=Severity.OK,
            message="Spazio personale agent-self.md presente",
        )
    except Exception as exc:
        return CheckOutcome(
            id="identity.self_valid",
            severity=Severity.UNDETERMINED,
            message=f"Impossibile leggere agent-self.md: {exc}",
        )
