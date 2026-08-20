"""Checks for environment, paths, and the state directory."""
from __future__ import annotations

from pathlib import Path

from nexgen_core.report import CheckOutcome, Severity


def check_state_dir(state_dir: Path) -> CheckOutcome:
    """Check the ~/.nexgen-engine state directory and create it if missing."""
    if not state_dir.is_dir():
        def remedy() -> bool:
            state_dir.mkdir(parents=True, exist_ok=True)
            return True

        return CheckOutcome(
            id="env.state_dir",
            severity=Severity.BROKEN,
            message=f"State directory '{state_dir}' does not exist.",
            action=f"Create the directory with: mkdir -p {state_dir}",
            remedy=remedy,
        )
    return CheckOutcome(
        id="env.state_dir",
        severity=Severity.OK,
        message=f"State directory present ({state_dir})",
    )


def check_vault_path(vault_path: Path) -> CheckOutcome:
    """Check that the Vault path exists."""
    if not vault_path.is_dir():
        return CheckOutcome(
            id="env.vault_path",
            severity=Severity.BROKEN,
            message=f"Knowledge Vault folder '{vault_path}' was not found.",
            action="Check the KNOWLEDGE_VAULT_PATH environment variable or clone the Vault.",
        )
    return CheckOutcome(
        id="env.vault_path",
        severity=Severity.OK,
        message=f"Knowledge Vault found at {vault_path}",
    )
