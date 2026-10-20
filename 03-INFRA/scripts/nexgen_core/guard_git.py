"""Converges the data repository's Git state before a guard or apply cycle writes.

One job: auto-commit pending infra files, report the untracked ones, inspect
the Vault repository against its authoritative remote and bring it to a state
an apply may run on -- fast-forward when behind, rebase when diverged, or
quarantine the local commits when the rebase cannot, and refuse the cycle
when the state does not allow a write at all.

It is the safety gate in front of every write phase: guessing here means
applying derived files on top of a history the remote does not have. It used
to be `GuardRunner._phase_git`; the coordinator keeps a one-line method that
passes itself in, so an instance patch still replaces the step.
"""
from __future__ import annotations

from nexgen_core.action_notes import WARN
from nexgen_core.git_ops import (
    GitState,
    auto_commit_infra_files,
    fast_forward_merge,
    get_untracked_infra_files,
    inspect_git_state,
    quarantine_diverged_commits,
    run_git,
)
from nexgen_core.guard_result import GuardMode, GuardResult
from nexgen_core.i18n import t


def converge_git_phase(
    runner,
    mode: GuardMode,
    allow_offline: bool,
    auth_remote: str,
    branch: str,
    actions: list[str],
) -> GuardResult | None:
    """Inspects data git state and converges it (fast-forward / rebase /
    quarantine). Returns a result only when the cycle must stop here."""
    if mode == GuardMode.PREFLIGHT:
        return None
    # Auto-commit any pending tracked infra files so they don't block sync
    auto_ok, _auto_committed = auto_commit_infra_files(runner.vault_data)
    if not auto_ok:
        # inspect_git_state below will still block fail-closed on DIRTY,
        # but the message must name the real cause (commit failed), not
        # just "unsaved changes".
        actions.append(WARN + t("Infra auto-commit failed; the Git inspection below decides whether the cycle can proceed"))
    untracked_infra = get_untracked_infra_files(runner.vault_data)
    if untracked_infra:
        actions.append(t(
            "New infra files never committed ({count}): stage them with vault-push, or they stay local-only.",
            count=len(untracked_infra),
        ))

    git_status = inspect_git_state(
        runner.vault_data,
        expected_branch=branch,
        remote=auth_remote,
        allow_offline=allow_offline,
    )
    if not git_status.allows_apply:
        return GuardResult(
            success=False,
            mode=mode,
            message=t("Operation blocked by Git: {reason}", reason=git_status.message),
            exit_code=1,
        )
    if git_status.state == GitState.BEHIND:
        ff_ok, ff_msg = fast_forward_merge(runner.vault_data, remote=auth_remote, branch=branch)
        if not ff_ok:
            return GuardResult(
                success=False,
                mode=mode,
                message=t("Error during automatic update: {reason}", reason=ff_msg),
                exit_code=1,
            )
        actions.append(ff_msg)
    elif git_status.state == GitState.DIVERGED:
        rebase_res = run_git(runner.vault_data, "rebase", f"{auth_remote}/{branch}")
        if rebase_res.returncode == 0:
            actions.append(t("Realigned with {remote}/{branch} via rebase", remote=auth_remote, branch=branch))
        else:
            run_git(runner.vault_data, "rebase", "--abort")
            q_ok, _q_branch, q_msg = quarantine_diverged_commits(runner.vault_data, remote=auth_remote, branch=branch)
            if q_ok:
                actions.append(q_msg)
            else:
                return GuardResult(
                    success=False,
                    mode=mode,
                    message=t("Error during divergence resolution: {reason}", reason=q_msg),
                    exit_code=1,
                )
    elif git_status.state == GitState.FRESH:
        actions.append(t("Data state: {status}", status=git_status.message))
    elif git_status.state == GitState.AHEAD:
        actions.append(t("Data state: {status}", status=git_status.message))
    return None
