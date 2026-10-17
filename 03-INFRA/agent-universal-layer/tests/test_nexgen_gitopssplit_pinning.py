"""Pinning del comportamento attuale di git_ops prima dello split.

Solo repo git finti in tmp_path creati con git init, mai repo veri,
mai push, mai fetch di rete, mai modifiche fuori tmp. Fissa:
inspect_git_state su repo finto stabile, quarantine su commit
divergente finto stabile, publish che rifiuta senza remote con
errore chiaro, firme pubbliche invariate.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import git_ops  # noqa: E402
from nexgen_core.git_ops import GitState  # noqa: E402


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(path)],
        check=True, capture_output=True, timeout=30,
    )
    _run_git(path, "config", "user.email", "test@example.com")
    _run_git(path, "config", "user.name", "Test")
    _run_git(path, "config", "commit.gpgsign", "false")
    return path


def _commit_file(repo: Path, relpath: str, content: str, message: str) -> str:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run_git(repo, "add", relpath)
    _run_git(repo, "commit", "-q", "-m", message)
    return _run_git(repo, "rev-parse", "HEAD")


def _make_diverged_repo(path: Path) -> tuple[Path, str, str, str]:
    """Repo finto con main e origin/main veramente divergenti, tutto locale.

    Nessun remote configurato: refs/remotes/origin/main e' creato con
    update-ref, quindi nessun fetch/push di rete puo' partire.
    """
    repo = _init_repo(path)
    base = _commit_file(repo, "base.md", "base\n", "base")
    local_head = _commit_file(repo, "locale.md", "locale\n", "commit locale")
    _run_git(repo, "branch", "laterale", base)
    _run_git(repo, "checkout", "-q", "laterale")
    side_head = _commit_file(repo, "remoto.md", "remoto\n", "commit lato remoto")
    _run_git(repo, "checkout", "-q", "main")
    _run_git(repo, "branch", "-D", "laterale")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", side_head)
    assert _run_git(repo, "remote") == ""
    merge_base = _run_git(repo, "merge-base", "main", "origin/main")
    assert merge_base == base
    assert local_head != side_head
    return repo, base, local_head, side_head


def test_inspect_git_state_su_repo_finto_stabile(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "inspect-finto")
    _commit_file(repo, "note.md", "appunto\n", "seed")
    assert _run_git(repo, "remote") == ""
    first = git_ops.inspect_git_state(repo)
    assert first.state == GitState.REMOTE_MISSING
    assert first.branch == "main"
    assert "origin" in first.message.lower()
    assert first.message.strip() != ""
    assert first.allows_apply is False
    second = git_ops.inspect_git_state(repo)
    assert second == first
    local_only = git_ops.inspect_git_state(repo, remote="local")
    assert local_only.state == GitState.LOCAL_ONLY
    assert local_only == git_ops.inspect_git_state(repo, remote="local")


def test_quarantine_su_commit_divergente_finto_stabile(tmp_path: Path) -> None:
    repo, _base, local_head, remote_head = _make_diverged_repo(tmp_path / "quarantena-finta")
    ok, q_branch, message = git_ops.quarantine_diverged_commits(repo)
    assert ok is True
    assert q_branch.startswith("quarantine/diverged-")
    assert message.strip() != ""
    assert _run_git(repo, "rev-parse", "HEAD") == remote_head
    assert _run_git(repo, "rev-parse", q_branch) == local_head
    assert q_branch in git_ops.list_quarantine_branches(repo)
    # Stessa fixture ricostruita: stesso esito di forma, HEAD riallineata.
    repo2, _b2, local2, remote2 = _make_diverged_repo(tmp_path / "quarantena-finta-bis")
    ok2, q_branch2, _msg2 = git_ops.quarantine_diverged_commits(repo2)
    assert ok2 is True
    assert q_branch2.startswith("quarantine/diverged-")
    assert _run_git(repo2, "rev-parse", "HEAD") == remote2
    assert _run_git(repo2, "rev-parse", q_branch2) == local2


def test_publish_rifiuta_senza_remote_con_errore_chiaro(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "publish-finto")
    _commit_file(repo, "note.md", "appunto\n", "seed")
    assert _run_git(repo, "remote") == ""
    head_before = _run_git(repo, "rev-parse", "HEAD")
    ok, message = git_ops.publish_changes(repo)
    assert ok is False
    assert "origin" in message.lower()
    assert message.strip() != ""
    assert _run_git(repo, "rev-parse", "HEAD") == head_before
    assert git_ops.list_quarantine_branches(repo) == []
    # Seconda corsa stabile: stesso rifiuto, nessun tocco fuori tmp.
    ok2, message2 = git_ops.publish_changes(repo)
    assert (ok2, message2) == (ok, message)
    assert _run_git(repo, "rev-parse", "HEAD") == head_before


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(git_ops.inspect_git_state).parameters) == [
        "repo_dir", "expected_branch", "remote", "allow_offline",
    ]
    assert list(inspect.signature(git_ops.quarantine_diverged_commits).parameters) == [
        "repo_dir", "remote", "branch",
    ]
    assert list(inspect.signature(git_ops.publish_changes).parameters) == [
        "repo_dir", "branch", "remote", "mirrors", "commit_msg", "files_to_commit",
    ]
    public = {
        name for name, value in vars(git_ops).items()
        if callable(value) and not name.startswith("_")
        and getattr(value, "__module__", "") == git_ops.__name__
    }
    assert public == {
        "GitState", "GitStatusResult", "is_infra_file", "auto_commit_infra_files",
        "list_quarantine_branches", "quarantine_diverged_commits",
        "fast_forward_merge", "run_git", "normalize_remote_url",
        "engine_upstream_remotes", "assert_safe_git_refs", "resolve_remotes",
        "oldest_unpublished_commit_timestamp", "get_current_branch",
        "get_untracked_infra_files", "get_uncommitted_files",
        "check_conflicts_or_rebase", "inspect_git_state", "publish_changes",
    }
