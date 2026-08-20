"""Git integrity and alignment checks for the Vault."""
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

#: An unpublished commit older than this threshold (seconds) is a real
#: problem nobody chose: it simply sat there because 'vault-push' never
#: ran. Overridable for installations with a publishing cadence different
#: from the release.
STALE_UNPUBLISHED_SECONDS = int(os.environ.get("NEXGEN_STALE_UNPUBLISHED_SECONDS", str(2 * 60 * 60)))


def check_git_alignment(vault_data: Path, expected_branch: str | None = None) -> CheckOutcome:
    """Check that the Vault is clean and aligned with the authoritative remote."""
    branch = expected_branch or os.environ.get("KNOWLEDGE_VAULT_BRANCH") or get_current_branch(vault_data) or "main"
    auth_remote, _ = resolve_remotes(vault_data)
    res = inspect_git_state(vault_data, expected_branch=branch, remote=auth_remote)

    if res.state == GitState.FRESH or res.state == GitState.LOCAL_ONLY:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.OK,
            message=f"Data state aligned ({res.message})",
        )
    elif res.state == GitState.DIRTY:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=f"There are unsaved changes in the Vault ({len(res.uncommitted_files)} files).",
            action="Run 'vault-push' to save your changes before syncing.",
            detail=", ".join(res.uncommitted_files[:5]),
        )
    elif res.state == GitState.CONFLICTED:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message="There is an interrupted merge or rebase in the Vault.",
            action="Run 'git rebase --abort' or 'git merge --abort' inside the Vault.",
        )
    elif res.state == GitState.FETCH_FAILED or res.state == GitState.REMOTE_MISSING:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.UNDETERMINED,
            message=f"Could not check alignment with the remote server: {res.message}",
            action="Check the internet connection or the remote configuration.",
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
                    message=f"There are commits unpublished in the Vault for more than {age_hours}h (the oldest exceeds the {STALE_UNPUBLISHED_SECONDS // 3600}h threshold).",
                    action="Run 'vault-push' to publish them.",
                )
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.OK,
            message="The local Vault has new commits ready to be pushed.",
        )
    elif res.state == GitState.BEHIND:
        def remedy() -> bool:
            from nexgen_core.git_ops import fast_forward_merge
            ok, _ = fast_forward_merge(vault_data, auth_remote, expected_branch)
            return ok

        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=f"Remote {auth_remote} has new commits not yet downloaded.",
            action="Run 'agent-sync apply' or 'agent-sync pull' to align the Vault.",
            remedy=remedy,
        )
    else:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=f"Git misalignment in the Vault: {res.message}",
            action="Run 'agent-sync apply' to check and attempt alignment.",
        )


def check_mirror_alignment(vault_data: Path, expected_branch: str | None = None) -> list[CheckOutcome]:
    """Every mirror declared in resolve_remotes must stay aligned with the
    branch published on the authoritative remote. The canonical Vault is
    always the latter: mirrors are replicas, not sources of truth.

    An unreachable mirror is not a Vault failure: it is undetermined,
    because it could simply be offline right now.
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
                message=f"Could not check the alignment of mirror '{mirror}': the authoritative remote '{auth_remote}' is unreachable.",
            ))
            continue

        if run_git(vault_data, "fetch", "--prune", mirror, branch).returncode != 0:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.UNDETERMINED,
                message=f"Mirror '{mirror}' is unreachable, so its alignment could not be checked.",
            ))
            continue

        auth_head = run_git(vault_data, "rev-parse", f"{auth_remote}/{branch}").stdout.strip()
        mirror_head = run_git(vault_data, "rev-parse", f"{mirror}/{branch}").stdout.strip()

        if auth_head and auth_head == mirror_head:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.OK,
                message=f"Mirror '{mirror}' aligned with authoritative remote '{auth_remote}'",
            ))
        else:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.BROKEN,
                message=f"Mirror '{mirror}' is not aligned with the branch published on '{auth_remote}'.",
                action=f"Run 'git push {mirror} {branch}' from the Vault to realign it (the canonical Vault remains {auth_remote}).",
            ))
    return outcomes
