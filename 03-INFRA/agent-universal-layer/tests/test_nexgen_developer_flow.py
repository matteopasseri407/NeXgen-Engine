"""The maintainer uses developer; main advances only through release merges."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nexgen_core.checks.git_checks import check_engine_lane
from nexgen_core.lanes import check_ref
from nexgen_core.report import Severity


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "commit", "--allow-empty", "-m", "published history")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(tmp_path, "checkout", "-b", "developer")
    git(tmp_path, "update-ref", "refs/remotes/origin/developer", "HEAD")
    return tmp_path


def test_developer_accepts_direct_development_commits(repo):
    git(repo, "commit", "--allow-empty", "-m", "development")
    assert check_ref(repo, "developer")[0]


def test_main_accepts_a_release_merge_from_developer(repo):
    git(repo, "commit", "--allow-empty", "-m", "release: version and notes")
    git(repo, "checkout", "main")
    git(repo, "merge", "--no-ff", "developer", "-m", "Release merge")
    assert check_ref(repo, "main")[0]


def test_main_refuses_direct_commits(repo):
    git(repo, "checkout", "main")
    git(repo, "commit", "--allow-empty", "-m", "direct work")
    assert not check_ref(repo, "main")[0]


@pytest.mark.parametrize("branch", ["dev/engine", "dev/maintenance", "release/v9", "feat/fix", "draft/v9"])
def test_other_development_branches_are_refused(repo, branch):
    git(repo, "branch", branch)
    ok, problems = check_ref(repo, branch)
    assert not ok
    assert any("developer" in problem for problem in problems)


def test_developer_must_include_published_main(repo):
    git(repo, "checkout", "main")
    git(repo, "commit", "--allow-empty", "-m", "new published state")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "developer")
    assert not check_ref(repo, "developer")[0]


def test_doctor_accepts_work_in_progress_on_developer(repo):
    (repo / "wip.txt").write_text("local work", encoding="utf-8")
    git(repo, "add", "wip.txt")
    assert check_engine_lane(repo).severity == Severity.OK


def test_doctor_warns_on_work_in_progress_on_main(repo):
    git(repo, "checkout", "main")
    (repo / "wip.txt").write_text("local work", encoding="utf-8")
    git(repo, "add", "wip.txt")
    assert check_engine_lane(repo).severity == Severity.WARN


@pytest.mark.parametrize("body", ["", '{"sha":"' + "a" * 40 + '"}'])
def test_sync_uses_github_atomic_merge(monkeypatch, body):
    from nexgen_core import lanes

    calls = []

    def merge(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, body, "")

    monkeypatch.setattr(lanes.subprocess, "run", merge)
    assert lanes.sync_developer("owner/repo") == 0
    assert "base=developer" in calls[0]
    assert "head=main" in calls[0]
    assert not any("force" in item for item in calls[0])


@pytest.mark.parametrize("body", ['{"message":"Merge conflict"}', '{"message":"Branch not found"}'])
def test_sync_conflict_or_missing_branch_fails_without_retry(monkeypatch, body):
    from nexgen_core import lanes

    calls = []

    def merge(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, body, "HTTP error")

    monkeypatch.setattr(lanes.subprocess, "run", merge)
    assert lanes.sync_developer("owner/repo") == 1
    assert len(calls) == 1


def test_sync_rejects_unverified_api_response(monkeypatch):
    from nexgen_core import lanes

    monkeypatch.setattr(lanes.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0, "{}", ""))
    assert lanes.sync_developer("owner/repo") == 1


def test_sync_rejects_invalid_repository_before_call(monkeypatch):
    from nexgen_core import lanes

    monkeypatch.setattr(lanes.subprocess, "run", lambda *a, **kw: pytest.fail("API must not run"))
    assert lanes.sync_developer("../repo") == 1
