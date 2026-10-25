# nexgen: one-job reason="decide whether this vault checkout may write to Git, and converge it read-only when it may not"
"""The vault's one write path: which checkout may commit, and what a reader does.

A server keeps more than one copy of the vault: the bare repository, the
worktree the vault-library serves, and the clone the OpenClaw gateway reads.
Only the served worktree has a person behind it, through the vault-library
tools. The clone is a reader, and a reader that commits diverges: that is how
four unpublished commits ended up on a server next to a history that could no
longer be fast-forwarded.

The question is answered once here, for the guard and for the doctor. A reader
never auto-commits, never commits on its branch and never pushes: it pulls with
`--ff-only`, and whatever it finds locally goes to the quarantine branch the
engine already has, so nothing is lost and one line can say what happened.
"""
from __future__ import annotations

import os
import secrets
import time
from collections.abc import Mapping
from pathlib import Path

from nexgen_core import git_ops, roles
from nexgen_core.action_notes import WARN
from nexgen_core.git_ops import GitState
from nexgen_core.i18n import t
from nexgen_core.stack.secrets import read_env_file
from nexgen_core.stack.services import env_file as stack_env_file

#: What the read-only clone's own commit says, when local work has to be parked.
QUARANTINE_MESSAGE = "quarantine: local changes found in a read-only vault clone"

#: The worktree the stack mounts when nothing overrides it, from
#: `03-INFRA/deploy/vault-mcp/provision-vault-repo.sh` and its compose file.
DEFAULT_STACK_WORKTREE = Path("/opt/knowledge-vault")

#: What only `bootstrap-vps.sh` writes into the stack's `.env`: the host uid the
#: container runs as. A workstation that started the stack with `nexgen stack up`
#: writes the secrets and not this, so the documented default is trusted only on
#: the machine that was bootstrapped to serve the Vault.
STACK_SERVER_FINGERPRINT = "VAULT_MCP_UID"


def served_worktree(engine_root: Path | None = None, env: Mapping[str, str] | None = None) -> Path | None:
    """The checkout this machine's vault-library serves, when it serves one here.

    Declared by the stack's own `.env` (the variable docker compose mounts) or
    by the environment; on a machine bootstrapped to serve the Vault, the
    documented default when the file names no path. None means "no vault-library
    serves the Vault from here", which is the ordinary PC and must keep behaving
    as it always did.
    """
    values = os.environ if env is None else env
    declared: dict[str, str] = {}
    if engine_root is not None:
        try:
            declared = read_env_file(stack_env_file(engine_root))
        except OSError:  # nexgen: allow reason="an unreadable stack .env means nothing is declared here, and silence keeps the ordinary PC unchanged"
            declared = {}
    for key in ("VAULT_WORKTREE_DIR", "VAULT_ROOT"):
        value = declared.get(key) or values.get(key) or ""
        if value.strip():
            return Path(value.strip()).expanduser()
    if declared.get(STACK_SERVER_FINGERPRINT):
        return DEFAULT_STACK_WORKTREE
    return None


def _same_path(left: Path, right: Path) -> bool:
    """Whether two paths name the same folder, without requiring it to exist."""
    try:
        return left.resolve() == right.resolve()
    except OSError:  # nexgen: allow reason="a path that cannot be resolved is compared as written"
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def readonly_reason(
    vault_data: Path,
    home: Path | None = None,
    engine_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Why this checkout must not write to Git, or None when it is the writer.

    Two facts decide it, both about this machine rather than about a name: the
    role the roles code recorded here, and a vault-library that serves another
    checkout. A PC that has neither keeps committing exactly as before.
    """
    if roles.declared_role(home=home, vault_root=vault_data) == roles.SERVER_ROLE:
        return t("the server role owns this machine, and its notes are written with the vault-library")
    served = served_worktree(engine_root, env)
    if served is not None and not _same_path(served, Path(vault_data)):
        return t("the vault-library here serves {path}, which is another checkout", path=str(served))
    return None


def pull_fast_forward(vault_data: Path, remote: str = "origin", branch: str = "main") -> tuple[bool, str]:
    """`git pull --ff-only`: the only way a read-only clone moves.

    A fast-forward cannot rewrite what is already there. When it is refused
    something local is in the way, and the caller quarantines it instead of
    forcing the tree to match.
    """
    try:
        git_ops.assert_safe_git_refs(remote, branch)
    except ValueError as exc:
        return False, t("Refusing unsafe git ref: {error}", error=exc)
    result = git_ops.run_git(vault_data, "pull", "--ff-only", remote, branch)
    if result.returncode == 0:
        return True, t("Vault clone updated with a fast-forward from {ref}", ref=f"{remote}/{branch}")
    detail = (result.stderr or result.stdout).strip().splitlines()
    return False, t(
        "Vault clone not updated ({detail}): the notes it reads may be stale",
        detail=detail[-1] if detail else f"exit {result.returncode}",
    )


def quarantine_local_changes(
    vault_data: Path, remote: str = "origin", branch: str = "main"
) -> tuple[bool, str, str]:
    """Parks uncommitted work on a `quarantine/diverged-*` branch, then realigns.

    The work becomes one commit there, so nothing is deleted, and the local
    branch goes back to the remote's commit. On a failure the tree is put back
    where it was found, with the same changes still in it.
    """
    try:
        git_ops.assert_safe_git_refs(remote, branch)
    except ValueError as exc:
        return False, "", t("Refusing unsafe git ref: {error}", error=exc)
    current = git_ops.get_current_branch(vault_data)
    if current != branch:
        return False, "", t(
            "Refusing to quarantine: HEAD is on '{found}', not '{expected}'",
            found=current or t("detached HEAD"), expected=branch,
        )
    status = git_ops.run_git(vault_data, "status", "--porcelain=v1", "-z")
    if status.returncode != 0:
        return False, "", t("Cannot quarantine: git status unreadable ({error})", error=status.stderr.strip())
    if not status.stdout.strip():
        return False, "", t("Nothing local to quarantine")

    q_branch = f"quarantine/diverged-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    created = git_ops.run_git(vault_data, "checkout", "-b", q_branch)
    if created.returncode != 0:
        return False, "", t(
            "Failed to create quarantine branch {branch}: {error}",
            branch=q_branch, error=created.stderr.strip(),
        )
    staged = git_ops.run_git(vault_data, "add", "-A")
    committed = (
        git_ops.run_git(vault_data, "commit", "-m", QUARANTINE_MESSAGE)
        if staged.returncode == 0 else staged
    )
    if committed.returncode != 0:
        # The changes are still in the tree: unstage them and step back, so the
        # next attempt (or a person) finds exactly what was there before.
        git_ops.run_git(vault_data, "reset")
        git_ops.run_git(vault_data, "checkout", branch)
        return False, q_branch, t(
            "Local changes could not be recorded on {branch}; nothing was lost and the working tree is unchanged",
            branch=q_branch,
        )
    back = git_ops.run_git(vault_data, "checkout", branch)
    realigned = (
        git_ops.run_git(vault_data, "reset", "--hard", f"{remote}/{branch}")
        if back.returncode == 0 else back
    )
    if realigned.returncode != 0:
        return False, q_branch, t(
            "Local changes are safe on '{branch}', but realigning {target} failed: {error}",
            branch=q_branch, target=f"{remote}/{branch}", error=realigned.stderr.strip(),
        )
    return True, q_branch, t(
        "Local changes parked on quarantine branch '{branch}': notes are written with the vault-library, not with git.",
        branch=q_branch,
    )


def unpublished_count(vault_data: Path, remote: str, branch: str) -> int | None:
    """How many commits `branch` holds that the remote does not, or None if git cannot tell."""
    result = git_ops.run_git(vault_data, "rev-list", "--count", f"{remote}/{branch}..{branch}")
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:  # nexgen: allow reason="git answered something that is not a count, which the caller reads as 'cannot tell'"
        return None


def converge_readonly(
    vault_data: Path,
    remote: str,
    branch: str,
    actions: list[str],
    reason: str,
) -> None:
    """Brings a read-only clone up to date without ever committing on its branch.

    Pull when it is behind, park what is local on a quarantine branch first
    when it is not, and never push. A clone that cannot be realigned keeps its
    work and says so: a stale copy is a warning, not a reason to stop the
    alignment of the whole machine.
    """
    if remote in ("local", "none"):
        actions.append(t(
            "Vault clone read-only ({reason}): there is no remote to pull from", reason=reason,
        ))
        return
    for _attempt in range(2):
        state = git_ops.inspect_git_state(vault_data, expected_branch=branch, remote=remote)
        if state.state == GitState.DIRTY:
            ok, _parked, message = quarantine_local_changes(vault_data, remote=remote, branch=branch)
            actions.append(message)
            if not ok:
                return
            continue
        if state.state in (GitState.AHEAD, GitState.DIVERGED):
            ok, _parked, message = git_ops.quarantine_diverged_commits(vault_data, remote=remote, branch=branch)
            actions.append(message)
            if not ok:
                return
            continue
        if state.state in (GitState.FRESH, GitState.BEHIND):
            ok, message = pull_fast_forward(vault_data, remote=remote, branch=branch)
            actions.append(message if ok else WARN + message)
            return
        actions.append(WARN + t(
            "Vault clone read-only, and it could not be realigned: {message}", message=state.message,
        ))
        return
