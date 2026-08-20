"""Controlli di integrità e allineamento Git per il Vault."""
from __future__ import annotations

from pathlib import Path

from nexgen_core.git_ops import GitState, inspect_git_state, resolve_remotes
from nexgen_core.report import CheckOutcome, Severity


def check_git_alignment(vault_data: Path, expected_branch: str = "main") -> CheckOutcome:
    """Verifica che il Vault sia pulito e allineato con il remoto autoritativo."""
    auth_remote, _ = resolve_remotes(vault_data)
    res = inspect_git_state(vault_data, expected_branch=expected_branch, remote=auth_remote)

    if res.state == GitState.FRESH or res.state == GitState.LOCAL_ONLY:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.OK,
            message=f"Stato dati allineato ({res.message})",
        )
    elif res.state == GitState.DIRTY:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=f"Ci sono modifiche non salvate nel Vault ({len(res.uncommitted_files)} file).",
            action="Esegui 'vault-push' per salvare le tue modifiche prima di sincronizzare.",
            detail=", ".join(res.uncommitted_files[:5]),
        )
    elif res.state == GitState.CONFLICTED:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message="È presente un'operazione di merge o rebase interrotta nel Vault.",
            action="Esegui 'git rebase --abort' o 'git merge --abort' all'interno del Vault.",
        )
    elif res.state == GitState.FETCH_FAILED or res.state == GitState.REMOTE_MISSING:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.UNDETERMINED,
            message=f"Impossibile verificare l'allineamento con il server remoto: {res.message}",
            action="Controlla la connessione internet o la configurazione del remoto.",
        )
    elif res.state == GitState.AHEAD:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.OK,
            message="Il Vault locale ha nuovi commit pronti per essere inviati.",
        )
    elif res.state == GitState.BEHIND:
        def remedy() -> bool:
            from nexgen_core.git_ops import fast_forward_merge
            ok, _ = fast_forward_merge(vault_data, auth_remote, expected_branch)
            return ok

        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=f"Il remoto {auth_remote} ha nuovi commit non ancora scaricati.",
            action="Esegui 'agent-sync apply' o 'agent-sync pull' per allineare il Vault.",
            remedy=remedy,
        )
    else:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=f"Disallineamento Git nel Vault: {res.message}",
            action="Esegui 'agent-sync apply' per verificare e tentare l'allineamento.",
        )
