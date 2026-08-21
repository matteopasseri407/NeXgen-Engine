"""Safe Git operations and remote management for NeXgen Engine v2.

Non-negotiable safety rules:
1. A working tree with unsaved user changes (dirty) is NEVER touched.
2. If there's a conflict or a rebase in progress, the operation stops immediately and states the abort command.
3. The authoritative push uses fetch + divergence check, with a clean rebase before publishing.
4. Configured mirrors are updated after the primary remote; a mirror failure never invalidates the primary.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from nexgen_core.i18n import t


class GitState(str, Enum):
    FRESH = "fresh"
    LOCAL_ONLY = "local_only"
    WRONG_BRANCH = "wrong_branch"
    CONFLICTED = "conflicted"
    DIRTY = "dirty"
    REMOTE_MISSING = "remote_missing"
    FETCH_FAILED = "fetch_failed"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    ERROR = "error"


@dataclass(frozen=True)
class GitStatusResult:
    state: GitState
    message: str
    branch: str = ""
    uncommitted_files: list[str] = field(default_factory=list)

    @property
    def allows_apply(self) -> bool:
        return self.state in {GitState.FRESH, GitState.LOCAL_ONLY, GitState.AHEAD, GitState.BEHIND}


def fast_forward_merge(repo_dir: Path, remote: str = "origin", branch: str = "main") -> tuple[bool, str]:
    """Performs a safe fast-forward merge (--ff-only) when the state is BEHIND."""
    res = run_git(repo_dir, "merge", "--ff-only", f"{remote}/{branch}")
    if res.returncode == 0:
        return True, t("Data updated successfully via fast-forward from {ref}", ref=f"{remote}/{branch}")
    return False, t("Fast-forward failed: {error}", error=res.stderr.strip())


def run_git(repo_dir: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Safely runs a git command with a timeout and captured output."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


def resolve_remotes(vault_data: Path) -> tuple[str, list[str]]:
    """Resolves the authoritative remote and mirrors from the environment or sync/remotes.yaml."""
    env_remote = os.environ.get("KNOWLEDGE_VAULT_REMOTE")
    env_mirrors = os.environ.get("KNOWLEDGE_VAULT_MIRRORS")

    auth_remote = "origin"
    mirrors: list[str] = []

    remotes_file = vault_data / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml"
    if remotes_file.is_file():
        try:
            data = yaml.safe_load(remotes_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                auth_remote = str(data.get("authoritative_remote", "origin"))
                raw_mirrors = data.get("mirrors", [])
                if isinstance(raw_mirrors, list):
                    mirrors = [str(m) for m in raw_mirrors if str(m).strip()]
        except Exception:
            pass

    # Override from environment variables
    if env_remote:
        auth_remote = env_remote.strip()
    if env_mirrors:
        mirrors = [m.strip() for m in env_mirrors.split(",") if m.strip()]

    return auth_remote, mirrors


def oldest_unpublished_commit_timestamp(repo_dir: Path, remote: str, branch: str) -> float | None:
    """Unix timestamp (commit time) of the oldest commit not yet published to
    `remote`. None if there's no commit ahead, or if git can't determine it
    (missing branch/remote, invalid refname)."""
    res = run_git(repo_dir, "log", "--reverse", "-1", "--format=%ct", f"{remote}/{branch}..HEAD")
    if res.returncode != 0:
        return None
    line = res.stdout.strip().splitlines()[0] if res.stdout.strip() else ""
    if not line.isdigit():
        return None
    return float(line)


def get_current_branch(repo_dir: Path) -> str:
    """Returns the current branch name, or an empty string if detached."""
    r = run_git(repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD")
    if r.returncode == 0:
        return r.stdout.strip()
    return ""


def get_uncommitted_files(repo_dir: Path) -> list[str]:
    """The tracked files with changes not yet committed.

    Untracked files are deliberately left out: a new file nobody has staged
    yet is not work at risk, and treating it as such would block the cycle
    over every scratch file left in the folder.
    """
    r = run_git(repo_dir, "status", "--porcelain", "--untracked-files=no")
    if r.returncode != 0 or not r.stdout.strip():
        return []
    files: list[str] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if len(line) > 3:
            files.append(line[3:].strip())
    return files


def check_conflicts_or_rebase(repo_dir: Path) -> str | None:
    """Checks whether a rebase or merge is in progress."""
    git_dir_r = run_git(repo_dir, "rev-parse", "--git-dir")
    if git_dir_r.returncode != 0:
        return None
    git_dir = Path(git_dir_r.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_dir / git_dir

    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return t("a git rebase is in progress in the vault: run 'git rebase --abort' before continuing")
    if (git_dir / "MERGE_HEAD").exists():
        return t("a git merge is in progress in the vault: run 'git merge --abort' before continuing")
    return None


def inspect_git_state(
    repo_dir: Path,
    expected_branch: str = "main",
    remote: str = "origin",
    allow_offline: bool = False
) -> GitStatusResult:
    """Full Git state inspection, per the transactional contract."""
    if remote in ("local", "none"):
        return GitStatusResult(GitState.LOCAL_ONLY, t("Local-Only mode"), expected_branch)

    # 1. Conflicts or pending operations
    conflict_msg = check_conflicts_or_rebase(repo_dir)
    if conflict_msg:
        return GitStatusResult(GitState.CONFLICTED, conflict_msg, expected_branch)

    # 2. Branch check
    curr_branch = get_current_branch(repo_dir)
    if not curr_branch or curr_branch != expected_branch:
        found = curr_branch or t("detached HEAD")
        return GitStatusResult(
            GitState.WRONG_BRANCH,
            t("Current branch '{found}', expected '{expected}'", found=found, expected=expected_branch),
            curr_branch
        )

    # 3. Uncommitted files (dirty)
    uncommitted = get_uncommitted_files(repo_dir)
    if uncommitted:
        return GitStatusResult(
            GitState.DIRTY,
            t("Unsaved changes on {count} tracked files", count=len(uncommitted)),
            curr_branch,
            uncommitted
        )

    # 4. Check that the remote exists
    if run_git(repo_dir, "remote", "get-url", remote).returncode != 0:
        if allow_offline:
            return GitStatusResult(GitState.FRESH, t("Offline manually allowed (remote missing)"), curr_branch)
        return GitStatusResult(
            GitState.REMOTE_MISSING,
            t("Authoritative remote '{remote}' not configured", remote=remote),
            curr_branch
        )

    # 5. Fetch from the remote
    fetch_res = run_git(repo_dir, "fetch", "--prune", remote, expected_branch)
    if fetch_res.returncode != 0:
        if allow_offline:
            return GitStatusResult(GitState.FRESH, t("Offline manually allowed"), curr_branch)
        detail = (fetch_res.stderr or fetch_res.stdout).strip()
        ref = f"{remote}/{expected_branch}"
        msg = (
            t("Could not reach remote {ref}: {detail}", ref=ref, detail=detail)
            if detail else t("Could not reach remote {ref}", ref=ref)
        )
        return GitStatusResult(GitState.FETCH_FAILED, msg, curr_branch)

    # 6. Compare local and remote commits
    lh_res = run_git(repo_dir, "rev-parse", expected_branch)
    rh_res = run_git(repo_dir, "rev-parse", f"{remote}/{expected_branch}")

    if lh_res.returncode != 0:
        return GitStatusResult(GitState.ERROR, t("Local branch '{branch}' not found", branch=expected_branch), curr_branch)

    # If the remote branch doesn't exist yet (e.g. first push to a new repository)
    if rh_res.returncode != 0:
        return GitStatusResult(
            GitState.AHEAD,
            t("Local branch '{branch}' is not on {remote} yet", branch=expected_branch, remote=remote),
            curr_branch,
        )

    mb_res = run_git(repo_dir, "merge-base", expected_branch, f"{remote}/{expected_branch}")
    if mb_res.returncode != 0:
        return GitStatusResult(
            GitState.ERROR,
            t("Error computing merge-base with {ref}", ref=f"{remote}/{expected_branch}"),
            curr_branch
        )

    lh, rh, mb = lh_res.stdout.strip(), rh_res.stdout.strip(), mb_res.stdout.strip()

    if lh == rh:
        return GitStatusResult(GitState.FRESH, t("Data already aligned"), curr_branch)
    elif mb == lh:
        return GitStatusResult(GitState.BEHIND, t("Remote {remote} has new commits (update available)", remote=remote), curr_branch)
    elif mb == rh:
        return GitStatusResult(GitState.AHEAD, t("Local branch has commits not yet sent to {remote}", remote=remote), curr_branch)
    else:
        return GitStatusResult(
            GitState.DIVERGED,
            t("Local branch has diverged from {remote} (manual resolution required)", remote=remote),
            curr_branch,
        )


def publish_changes(
    repo_dir: Path,
    branch: str = "main",
    remote: str = "origin",
    mirrors: list[str] | None = None,
    commit_msg: str | None = None,
    files_to_commit: list[str] | None = None
) -> tuple[bool, str]:
    """Commits the work and publishes it to the authoritative remote and the mirrors.

    The local commit always happens, even in Local-Only mode: there's no
    remote to push to there, but the work still needs to be made safe in
    history. Skipping the commit too would mean reporting "done" to someone
    whose work no longer exists anywhere.
    """
    committed = False
    published = False

    # The commit happens first, before any consideration of the remote.
    if commit_msg:
        if files_to_commit:
            add_res = run_git(repo_dir, "add", "--", *files_to_commit)
            if add_res.returncode != 0:
                return False, t("git add failed: {error}", error=add_res.stderr)

        # Check whether there's anything staged to commit
        staged_check = run_git(repo_dir, "diff", "--cached", "--quiet")
        if staged_check.returncode != 0:
            c_res = run_git(repo_dir, "commit", "-m", commit_msg)
            if c_res.returncode != 0:
                return False, t("git commit failed: {error}", error=c_res.stderr)
            committed = True

    if remote in ("local", "none"):
        if committed:
            return True, t("Local commit done (Local-Only mode: no remote to update)")
        return True, t("Nothing to commit (Local-Only mode: no remote to update)")

    # Fetch and verify before pushing
    fetch_res = run_git(repo_dir, "fetch", "--prune", remote, branch)
    if fetch_res.returncode != 0:
        if committed:
            return False, t(
                "{remote} unreachable: the commit stays local, publish it later with 'vault-push'",
                remote=remote,
            )
        return False, t("Could not reach {remote} for publishing", remote=remote)

    lh = run_git(repo_dir, "rev-parse", branch).stdout.strip()
    rh = run_git(repo_dir, "rev-parse", f"{remote}/{branch}").stdout.strip()
    mb = run_git(repo_dir, "merge-base", branch, f"{remote}/{branch}").stdout.strip()

    if lh != rh:
        if mb == rh:
            # We're ahead: normal push
            p_res = run_git(repo_dir, "push", remote, branch)
            if p_res.returncode != 0:
                return False, t("Push to {remote} failed: {error}", remote=remote, error=p_res.stderr)
            published = True
        elif mb == lh:
            return False, t("Could not push: local branch is behind {remote}", remote=remote)
        else:
            # Divergence. An automatic rebase over a tree with uncommitted
            # changes would put work nobody entrusted to us at risk: better
            # to stop and say which files are blocking it.
            dirty = get_uncommitted_files(repo_dir)
            if dirty:
                shown = ", ".join(dirty[:5]) + ("..." if len(dirty) > 5 else "")
                return False, t(
                    "Data has diverged from {remote}, and there are uncommitted changes "
                    "({files}). Not realigning automatically: commit them or stash them, then retry",
                    remote=remote, files=shown,
                )
            rebase_res = run_git(repo_dir, "rebase", f"{remote}/{branch}")
            if rebase_res.returncode == 0:
                p_res = run_git(repo_dir, "push", remote, branch)
                if p_res.returncode != 0:
                    return False, t("Push after rebase failed: {error}", error=p_res.stderr)
                published = True
            else:
                run_git(repo_dir, "rebase", "--abort")
                return False, t("Data has diverged from {remote}, automatic rebase did not succeed", remote=remote)

    # Mirror update (best effort)
    if mirrors:
        for mirror in mirrors:
            m_res = run_git(repo_dir, "push", mirror, branch)
            if m_res.returncode != 0:
                # Retry with force-with-lease after a fetch
                run_git(repo_dir, "fetch", "--prune", mirror, branch)
                run_git(repo_dir, "push", "--force-with-lease", mirror, branch)

    if committed or published:
        return True, t("Published successfully")
    return True, t("Nothing to publish")
