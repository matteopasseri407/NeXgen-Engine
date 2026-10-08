"""Git integrity and alignment checks for the Vault."""
from __future__ import annotations

import os
import time
from pathlib import Path

from nexgen_core.git_ops import (
    GitState,
    engine_upstream_remotes,
    get_current_branch,
    get_uncommitted_files,
    inspect_git_state,
    list_quarantine_branches,
    oldest_unpublished_commit_timestamp,
    resolve_remotes,
    run_git,
)
from nexgen_core.i18n import t
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
            message=t("Data state aligned ({message})", message=res.message),
        )
    elif res.state == GitState.DIRTY:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=t("There are unsaved changes in the Vault ({count} files).", count=len(res.uncommitted_files)),
            action=t("Stage them first ('git add <file>'), then run 'vault-push' to publish them."),
            detail=", ".join(res.uncommitted_files[:5]),
        )
    elif res.state == GitState.CONFLICTED:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=t("There is an interrupted merge or rebase in the Vault."),
            action=t("Run 'git rebase --abort' or 'git merge --abort' inside the Vault."),
        )
    elif res.state == GitState.FETCH_FAILED or res.state == GitState.REMOTE_MISSING:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.UNDETERMINED,
            message=t("Could not check alignment with the remote server: {message}", message=res.message),
            action=t("Check the internet connection or the remote configuration."),
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
                    message=t(
                        "There are commits unpublished in the Vault for more than {age_hours}h "
                        "(the oldest exceeds the {threshold_hours}h threshold).",
                        age_hours=age_hours, threshold_hours=STALE_UNPUBLISHED_SECONDS // 3600,
                    ),
                    action=t("Run 'vault-push' to publish them."),
                )
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.OK,
            message=t("The local Vault has new commits ready to be pushed."),
        )
    elif res.state == GitState.BEHIND:
        def remedy() -> bool:
            from nexgen_core.git_ops import fast_forward_merge
            ok, _ = fast_forward_merge(vault_data, auth_remote, branch)
            return ok

        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=t("Remote {remote} has new commits not yet downloaded.", remote=auth_remote),
            action=t("Run 'agent-sync apply' or 'agent-sync pull' to align the Vault."),
            remedy=remedy,
        )
    else:
        return CheckOutcome(
            id="git.alignment",
            severity=Severity.BROKEN,
            message=t("Git misalignment in the Vault: {message}", message=res.message),
            action=t("Run 'agent-sync apply' to check and attempt alignment."),
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
                message=t(
                    "Could not check the alignment of mirror '{mirror}': the authoritative remote '{remote}' is unreachable.",
                    mirror=mirror, remote=auth_remote,
                ),
            ))
            continue

        if run_git(vault_data, "fetch", "--prune", mirror, branch).returncode != 0:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.UNDETERMINED,
                message=t("Mirror '{mirror}' is unreachable, so its alignment could not be checked.", mirror=mirror),
            ))
            continue

        auth_head = run_git(vault_data, "rev-parse", f"{auth_remote}/{branch}").stdout.strip()
        mirror_head = run_git(vault_data, "rev-parse", f"{mirror}/{branch}").stdout.strip()

        if auth_head and auth_head == mirror_head:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.OK,
                message=t("Mirror '{mirror}' aligned with authoritative remote '{remote}'", mirror=mirror, remote=auth_remote),
            ))
        else:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.BROKEN,
                message=t(
                    "Mirror '{mirror}' is not aligned with the branch published on '{remote}'.",
                    mirror=mirror, remote=auth_remote,
                ),
                action=t(
                    "Run 'git push {mirror} {branch}' from the Vault to realign it (the canonical Vault remains {remote}).",
                    mirror=mirror, branch=branch, remote=auth_remote,
                ),
            ))
    return outcomes


def check_remotes_config(vault_data: Path) -> CheckOutcome | None:
    """The remotes file must parse to a usable declaration.

    A malformed ``remotes.yaml`` falls back to ``origin`` in silence, which
    can publish to the wrong remote. Missing file is fine (convention over
    configuration); present-but-unreadable is BROKEN, never silent. Returns
    None when there is nothing to say.
    """
    import yaml

    path = vault_data / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return CheckOutcome(
            id="git.remotes_config",
            severity=Severity.BROKEN,
            message=t("The remotes file {path} is unreadable ({error}); pushes would silently fall back to 'origin'.", path=path, error=exc),
            action=t("Fix the YAML syntax of remotes.yaml."),
        )
    if data is not None and not isinstance(data, dict):
        return CheckOutcome(
            id="git.remotes_config",
            severity=Severity.BROKEN,
            message=t("The remotes file {path} does not contain a map; pushes would silently fall back to 'origin'.", path=path),
            action=t("Fix the structure of remotes.yaml (authoritative_remote, mirrors)."),
        )
    return CheckOutcome(
        id="git.remotes_config",
        severity=Severity.OK,
        message=t("Remotes declaration readable"),
    )


def check_vault_remote_privacy(vault_data: Path) -> CheckOutcome | None:
    """No remote of the vault may be the engine's public repository.

    The vault holds private notes, decisions and (never committed, but nearby) secrets; the
    engine's repository is public. A vault clone whose `origin` was left pointing at the engine,
    or a remote added by mistake, is one `vault push` from publishing all of it. Returns None
    when the vault is not a git repository (nothing to say).
    """
    if not (vault_data / ".git").exists():
        return None
    leaks = engine_upstream_remotes(vault_data)
    if not leaks:
        return CheckOutcome(
            id="git.vault_remote_privacy",
            severity=Severity.OK,
            message=t("No remote of the Vault points at the engine's public repository"),
        )
    shown = ", ".join(f"{name} ({url})" for name, url in leaks)
    return CheckOutcome(
        id="git.vault_remote_privacy",
        severity=Severity.BROKEN,
        message=t("The Vault has a remote that is the engine's public repository: {remotes}", remotes=shown),
        action=t("Point it at your own private repository ('git remote set-url <name> <url>') or remove it ('git remote remove <name>'). Publishing is refused until then."),
    )


def check_quarantine_branches(vault_data: Path) -> CheckOutcome:
    """Checks whether there are diverged quarantine branches awaiting reconciliation."""
    if not (vault_data / ".git").exists():
        return CheckOutcome(
            id="git.quarantine",
            severity=Severity.OK,
            message=t("No quarantine branches in the Vault"),
        )

    branches = list_quarantine_branches(vault_data)
    if not branches:
        return CheckOutcome(
            id="git.quarantine",
            severity=Severity.OK,
            message=t("No quarantine branches in the Vault"),
        )

    branch_list = ", ".join(branches)
    first_b = branches[0]
    return CheckOutcome(
        id="git.quarantine",
        severity=Severity.WARN,
        message=t(
            "Found {count} quarantine branch(es) with isolated diverged changes: {branches}",
            count=len(branches),
            branches=branch_list,
        ),
        action=t(
            "Review diff with 'git diff main..{branch}', reconcile changes into canonical files, then remove the quarantine branch with 'git branch -D {branch}'.",
            branch=first_b,
        ),
    )


def check_engine_lane(engine_root: Path) -> CheckOutcome | None:
    """Check the maintainer branch without mutating the installed engine."""
    from nexgen_core.lanes import check_ref, resolve_revision

    repo = engine_root.parent if engine_root.name == "03-INFRA" else engine_root
    if not (repo / ".git").exists():
        return None
    branch = get_current_branch(repo)
    if branch not in ("developer", "main"):
        return CheckOutcome(
            id="git.engine_lane", severity=Severity.WARN,
            message=t("Engine development belongs on developer (current branch: {branch}).", branch=branch or "HEAD"),
            action=t("Move the work to developer and run the engine test gate."),
        )
    dirty = get_uncommitted_files(repo)
    if branch == "main" and dirty:
        return CheckOutcome(
            id="git.engine_lane", severity=Severity.WARN,
            message=t("Engine development belongs on developer (current branch: {branch}).", branch=branch),
            detail=t("{count} uncommitted files", count=len(dirty)),
            action=t("Move the work to developer and run the engine test gate."),
        )
    required = f"origin/{branch}"
    if resolve_revision(repo, required) is None:
        return CheckOutcome(
            id="git.engine_lane", severity=Severity.UNDETERMINED,
            message=t("Could not check the engine lane: missing ref {ref}", ref=required),
            action=t("Fetch the engine repository's remote branches and run doctor again."),
        )
    ok, problems = check_ref(repo, branch)
    return CheckOutcome(
        id="git.engine_lane", severity=Severity.OK if ok else Severity.WARN,
        message=(t("Engine checkout on developer; development commits and local edits are allowed.")
                 if ok and branch == "developer" else t("Engine checkout on '{branch}', clean and aligned", branch=branch)
                 if ok else t("Engine branch check failed: {detail}", detail="; ".join(problems))),
        action="" if ok else t("Move the work to developer and run the engine test gate."),
    )
