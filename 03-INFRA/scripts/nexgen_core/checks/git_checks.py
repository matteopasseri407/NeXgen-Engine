"""Controlli di integrità e allineamento Git per il Vault."""
from __future__ import annotations

import os
import time
from pathlib import Path

from nexgen_core.git_ops import (
    GitState,
    get_current_branch,
    inspect_git_state,
    oldest_unpublished_commit_timestamp,
    resolve_remotes,
    run_git,
)
from nexgen_core.report import CheckOutcome, Severity

#: Un commit non pubblicato più vecchio di questa soglia (secondi) è un
#: problema reale che nessuno ha scelto: è semplicemente rimasto lì senza
#: che 'vault-push' venisse eseguito. Sovrascrivibile per installazioni con
#: una cadenza di pubblicazione diversa dalla release.
STALE_UNPUBLISHED_SECONDS = int(os.environ.get("NEXGEN_STALE_UNPUBLISHED_SECONDS", str(2 * 60 * 60)))


def check_git_alignment(vault_data: Path, expected_branch: str | None = None) -> CheckOutcome:
    """Verifica che il Vault sia pulito e allineato con il remoto autoritativo."""
    branch = expected_branch or os.environ.get("KNOWLEDGE_VAULT_BRANCH") or get_current_branch(vault_data) or "main"
    auth_remote, _ = resolve_remotes(vault_data)
    res = inspect_git_state(vault_data, expected_branch=branch, remote=auth_remote)

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
        oldest_ts = oldest_unpublished_commit_timestamp(vault_data, auth_remote, branch)
        if oldest_ts is not None:
            age_seconds = time.time() - oldest_ts
            if age_seconds > STALE_UNPUBLISHED_SECONDS:
                age_hours = int(age_seconds // 3600)
                return CheckOutcome(
                    id="git.alignment",
                    severity=Severity.BROKEN,
                    message=f"Ci sono commit non pubblicati nel Vault da più di {age_hours}h (il più vecchio supera la soglia di {STALE_UNPUBLISHED_SECONDS // 3600}h).",
                    action="Esegui 'vault-push' per pubblicarli.",
                )
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


def check_mirror_alignment(vault_data: Path, expected_branch: str | None = None) -> list[CheckOutcome]:
    """Ogni mirror dichiarato in resolve_remotes deve restare allineato al
    branch pubblicato sul remoto autoritativo. Il Vault canonico resta
    sempre quest'ultimo: i mirror sono repliche, non fonti di verità.

    Un mirror irraggiungibile non è un guasto del Vault: è undetermined,
    perché potrebbe essere semplicemente offline in questo momento.
    """
    if not (vault_data / ".git").exists():
        return []

    branch = expected_branch or os.environ.get("KNOWLEDGE_VAULT_BRANCH") or get_current_branch(vault_data) or "main"
    auth_remote, mirrors = resolve_remotes(vault_data)
    if auth_remote in ("local", "none") or not mirrors:
        return []

    outcomes: list[CheckOutcome] = []
    for mirror in mirrors:
        id_ = f"git.mirror_alignment.{mirror}"

        if run_git(vault_data, "fetch", "--prune", auth_remote, branch).returncode != 0:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.UNDETERMINED,
                message=f"Impossibile verificare l'allineamento del mirror '{mirror}': il remoto autoritativo '{auth_remote}' non è raggiungibile.",
            ))
            continue

        if run_git(vault_data, "fetch", "--prune", mirror, branch).returncode != 0:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.UNDETERMINED,
                message=f"Il mirror '{mirror}' non è raggiungibile per verificarne l'allineamento.",
            ))
            continue

        auth_head = run_git(vault_data, "rev-parse", f"{auth_remote}/{branch}").stdout.strip()
        mirror_head = run_git(vault_data, "rev-parse", f"{mirror}/{branch}").stdout.strip()

        if auth_head and auth_head == mirror_head:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.OK,
                message=f"Mirror '{mirror}' allineato al remoto autoritativo '{auth_remote}'",
            ))
        else:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.BROKEN,
                message=f"Il mirror '{mirror}' non è allineato al branch pubblicato su '{auth_remote}'.",
                action=f"Esegui 'git push {mirror} {branch}' dal Vault per riallinearlo (il Vault canonico resta {auth_remote}).",
            ))
    return outcomes
