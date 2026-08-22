"""Unit tests for auto-commit of infra files, quarantine of diverged commits, and doctor checks."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks.git_checks import check_quarantine_branches
from nexgen_core.git_ops import (
    auto_commit_infra_files,
    get_uncommitted_files,
    list_quarantine_branches,
    publish_changes,
    quarantine_diverged_commits,
    run_git,
)
from nexgen_core.report import Severity

pytestmark = pytest.mark.filterwarnings("ignore")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_auto_commit_infra_files(tmp_path: Path) -> None:
    repo = tmp_path / "vault"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    infra_file = repo / "03-INFRA" / "mcp" / "manifest.yaml"
    infra_file.parent.mkdir(parents=True)
    infra_file.write_text("servers: {}", encoding="utf-8")

    note_file = repo / "notes" / "note.md"
    note_file.parent.mkdir(parents=True)
    note_file.write_text("# Note 1", encoding="utf-8")

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial commit")

    # Modify both files without staging
    infra_file.write_text("servers:\n  test: full", encoding="utf-8")
    note_file.write_text("# Note 1 modified", encoding="utf-8")

    ok, committed = auto_commit_infra_files(repo)
    assert ok is True
    assert len(committed) == 1
    assert "03-INFRA/mcp/manifest.yaml" in committed[0].replace("\\", "/")

    # Infra file is committed, note file is still uncommitted
    dirty = get_uncommitted_files(repo)
    assert len(dirty) == 1
    assert "notes/note.md" in dirty[0].replace("\\", "/")


def test_quarantine_diverged_commits(tmp_path: Path) -> None:
    # 1. Setup bare remote
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)

    # 2. Clone A
    clone_a = tmp_path / "clone_a"
    subprocess.run(["git", "clone", str(remote), str(clone_a)], check=True)
    file_a = clone_a / "file.txt"
    file_a.write_text("base content\n", encoding="utf-8")
    _git(clone_a, "add", "file.txt")
    _git(clone_a, "commit", "-m", "base commit")
    _git(clone_a, "push", "origin", "main")

    # 3. Clone B
    clone_b = tmp_path / "clone_b"
    subprocess.run(["git", "clone", str(remote), str(clone_b)], check=True)

    # Clone A makes a commit and pushes
    file_a.write_text("clone A edit\n", encoding="utf-8")
    _git(clone_a, "commit", "-am", "commit from A")
    _git(clone_a, "push", "origin", "main")

    # Clone B makes a conflicting commit locally
    file_b = clone_b / "file.txt"
    file_b.write_text("clone B conflicting edit\n", encoding="utf-8")
    _git(clone_b, "commit", "-am", "commit from B")

    # Clone B fetches and discovers divergence
    _git(clone_b, "fetch", "origin", "main")

    # Quarantine diverged commits in Clone B
    ok, q_branch, msg = quarantine_diverged_commits(clone_b, remote="origin", branch="main")
    assert ok is True
    assert q_branch.startswith("quarantine/diverged-")

    # Clone B's main is now clean and aligned with origin/main (has clone A edit)
    assert file_b.read_text(encoding="utf-8") == "clone A edit\n"

    # Quarantine branch holds the clone B edit
    q_branches = list_quarantine_branches(clone_b)
    assert len(q_branches) == 1
    assert q_branch in q_branches[0]


def test_publish_changes_handles_conflict_via_quarantine(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)

    clone_a = tmp_path / "clone_a"
    subprocess.run(["git", "clone", str(remote), str(clone_a)], check=True)
    f_a = clone_a / "03-INFRA" / "conf.yaml"
    f_a.parent.mkdir(parents=True)
    f_a.write_text("key: value_base\n", encoding="utf-8")
    _git(clone_a, "add", "-A")
    _git(clone_a, "commit", "-m", "base")
    _git(clone_a, "push", "origin", "main")

    clone_b = tmp_path / "clone_b"
    subprocess.run(["git", "clone", str(remote), str(clone_b)], check=True)

    # Clone A pushes an update
    f_a.write_text("key: value_a\n", encoding="utf-8")
    _git(clone_a, "commit", "-am", "edit from A")
    _git(clone_a, "push", "origin", "main")

    # Clone B makes a conflicting change
    f_b = clone_b / "03-INFRA" / "conf.yaml"
    f_b.write_text("key: value_b\n", encoding="utf-8")
    _git(clone_b, "commit", "-am", "edit from B")

    # Publish in Clone B: should rebase, conflict, abort, quarantine, and reset cleanly
    success, msg = publish_changes(clone_b, branch="main", remote="origin")
    assert success is True
    assert "quarantine" in msg.lower()

    # Clone B's main has origin/main content
    assert f_b.read_text(encoding="utf-8") == "key: value_a\n"

    # Quarantine branch exists
    q_branches = list_quarantine_branches(clone_b)
    assert len(q_branches) == 1


def test_quarantine_preserves_uncommitted_tracked_changes(tmp_path: Path) -> None:
    """Divergence plus uncommitted tracked changes: the uncommitted work must
    survive in the quarantine branch, not be destroyed by the hard reset."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)

    clone_a = tmp_path / "clone_a"
    subprocess.run(["git", "clone", str(remote), str(clone_a)], check=True)
    f_a = clone_a / "03-INFRA" / "conf.yaml"
    f_a.parent.mkdir(parents=True)
    note_a = clone_a / "04-NOW" / "nota.md"
    note_a.parent.mkdir(parents=True)
    f_a.write_text("key: value_base\n", encoding="utf-8")
    note_a.write_text("appunti base\n", encoding="utf-8")
    _git(clone_a, "add", "-A")
    _git(clone_a, "commit", "-m", "base")
    _git(clone_a, "push", "origin", "main")

    clone_b = tmp_path / "clone_b"
    subprocess.run(["git", "clone", str(remote), str(clone_b)], check=True)
    _git(clone_b, "config", "user.email", "test@example.com")
    _git(clone_b, "config", "user.name", "Test")

    # Clone A pushes an update
    f_a.write_text("key: value_a\n", encoding="utf-8")
    _git(clone_a, "commit", "-am", "edit from A")
    _git(clone_a, "push", "origin", "main")

    # Clone B makes a conflicting commit, then edits a TRACKED note without committing
    f_b = clone_b / "03-INFRA" / "conf.yaml"
    f_b.write_text("key: value_b\n", encoding="utf-8")
    _git(clone_b, "commit", "-am", "edit from B")
    note_b = clone_b / "04-NOW" / "nota.md"
    note_b.write_text("appunti IMPORTANTI non committati\n", encoding="utf-8")

    success, msg = publish_changes(clone_b, branch="main", remote="origin")
    assert success is True
    assert "quarantine" in msg.lower()

    # Uncommitted work must be recoverable from the quarantine branch
    q_branches = list_quarantine_branches(clone_b)
    assert len(q_branches) == 1
    show = _git(clone_b, "show", f"{q_branches[0]}:04-NOW/nota.md")
    assert show.returncode == 0
    assert show.stdout == "appunti IMPORTANTI non committati\n"

    # Working tree is aligned with origin/main (note back to its committed version)
    assert note_b.read_text(encoding="utf-8") == "appunti base\n"


def test_doctor_reports_quarantine_branch(tmp_path: Path) -> None:
    repo = tmp_path / "vault"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    f = repo / "README.md"
    f.write_text("# Vault\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    # No quarantine branch initially
    outcome = check_quarantine_branches(repo)
    assert outcome.severity == Severity.OK

    # Create a quarantine branch
    _git(repo, "branch", "quarantine/diverged-20260822-120000")
    outcome = check_quarantine_branches(repo)
    assert outcome.severity == Severity.WARN
    assert "quarantine/diverged-20260822-120000" in outcome.message
    assert outcome.action is not None

    # Delete quarantine branch -> OK
    _git(repo, "branch", "-D", "quarantine/diverged-20260822-120000")
    outcome_after = check_quarantine_branches(repo)
    assert outcome_after.severity == Severity.OK
