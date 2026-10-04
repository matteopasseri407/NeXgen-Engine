"""Unit tests for the agent-lane contract: guard script + doctor watch."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks.git_checks import check_engine_lane  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "lane_guard_under_test", SCRIPTS_DIR / "lane_guard.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=False, env=env,
    )


def _repo_with_lanes(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "f.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "branch", "developer")
    return repo


def test_direct_fix_on_release_is_blocked(tmp_path: Path) -> None:
    lg = _load_guard()
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "branch", "release/v9", "developer")
    _git(repo, "checkout", "-q", "release/v9")
    (repo / "f.txt").write_text("b", encoding="utf-8")
    _git(repo, "commit", "-am", "fix council thing")
    ok, problems = lg.check_ref(repo, "release/v9")
    assert ok is False
    assert any("fix council thing" in problem for problem in problems)


def test_release_cut_from_developer_passes(tmp_path: Path) -> None:
    lg = _load_guard()
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "checkout", "-qb", "dev/x", "developer")
    (repo / "g.txt").write_text("c", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "lane work")
    _git(repo, "checkout", "-q", "developer")
    _git(repo, "merge", "--no-ff", "dev/x", "-m", "merge dev/x")
    _git(repo, "branch", "release/v9", "developer")
    ok, problems = lg.check_ref(repo, "release/v9")
    assert ok is True, problems


def test_release_chore_does_not_launder_history(tmp_path: Path) -> None:
    lg = _load_guard()
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "branch", "release/v9", "developer")
    _git(repo, "checkout", "-q", "release/v9")
    (repo / "f.txt").write_text("b", encoding="utf-8")
    _git(repo, "commit", "-am", "hotfix diretto")
    _git(repo, "commit", "--allow-empty", "-m", "release: notes for v9")
    ok, _problems = lg.check_ref(repo, "release/v9")
    assert ok is False


def test_allowed_release_chore_and_remote_only_integration(tmp_path: Path) -> None:
    lg = _load_guard()
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "checkout", "-qb", "release/v9", "developer")
    _git(repo, "commit", "--allow-empty", "-m", "release: notes for v9")
    assert lg.check_ref(repo, "release/v9")[0]
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--branch", "release/v9", str(repo), str(clone)], check=True, capture_output=True)
    assert lg.check_ref(clone, "release/v9")[0]


def test_release_must_descend_from_developer(tmp_path: Path) -> None:
    lg = _load_guard()
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "checkout", "-qb", "sideways", "main")
    (repo / "z.txt").write_text("z", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "sideways")
    _git(repo, "branch", "-f", "developer", "sideways")
    _git(repo, "branch", "release/v9", "main")
    ok, problems = lg.check_ref(repo, "release/v9")
    assert ok is False
    assert any("descend" in problem for problem in problems)


def test_unguarded_rungs_are_ignored(tmp_path: Path) -> None:
    lg = _load_guard()
    assert lg.guarded_ref("main") is True
    assert lg.guarded_ref("release/v2.3.10") is True
    assert lg.guarded_ref("developer") is True
    assert lg.guarded_ref("dev/engine") is False


def test_non_lane_branch_is_rejected(tmp_path: Path) -> None:
    lg = _load_guard()
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "checkout", "-qb", "feat/some-fix", "developer")
    (repo / "g.txt").write_text("c", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "some fix")
    ok, problems = lg.check_ref(repo, "feat/some-fix")
    assert ok is False
    assert any("dev/<agent>" in problem for problem in problems)


def test_dev_lane_branch_passes_guard(tmp_path: Path) -> None:
    lg = _load_guard()
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "checkout", "-qb", "dev/engine", "developer")
    (repo / "g.txt").write_text("c", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "lane work")
    ok, problems = lg.check_ref(repo, "dev/engine")
    assert ok is True, problems


def test_engine_lane_warns_on_non_lane_branch(tmp_path: Path) -> None:
    repo = _repo_with_lanes(tmp_path)
    engine_root = repo / "03-INFRA"
    engine_root.mkdir()
    _git(repo, "checkout", "-qb", "fix/quick-thing", "developer")
    (repo / "dirty.txt").write_text("wip", encoding="utf-8")
    _git(repo, "add", "-A")
    outcome = check_engine_lane(engine_root)
    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "dev/<agent>" in outcome.message


def test_engine_lane_warns_on_guarded_rung_with_work(tmp_path: Path) -> None:
    repo = _repo_with_lanes(tmp_path)
    engine_root = repo / "03-INFRA"
    engine_root.mkdir()
    _git(repo, "checkout", "-q", "main")
    (repo / "dirty.txt").write_text("wip", encoding="utf-8")
    _git(repo, "add", "-A")
    outcome = check_engine_lane(engine_root)
    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "dev/<agent>" in outcome.message


def test_engine_lane_ok_on_lane_branch_and_missing_checkout(tmp_path: Path) -> None:
    repo = _repo_with_lanes(tmp_path)
    engine_root = repo / "03-INFRA"
    engine_root.mkdir()
    _git(repo, "checkout", "-qb", "dev/test")
    outcome = check_engine_lane(engine_root)
    assert outcome is not None
    assert outcome.severity == Severity.OK
    assert check_engine_lane(tmp_path / "nope") is None


def test_developer_accepts_lane_merge_but_refuses_direct_commit(tmp_path):
    from nexgen_core.lanes import check_ref
    repo = _repo_with_lanes(tmp_path)
    base = _git(repo, "rev-parse", "developer").stdout.strip()
    _git(repo, "checkout", "-qb", "dev/x", "developer")
    _git(repo, "commit", "--allow-empty", "-m", "lane change")
    _git(repo, "checkout", "developer")
    _git(repo, "merge", "--no-ff", "dev/x", "-m", "merge lane")
    assert check_ref(repo, "developer", base=base)[0]
    _git(repo, "commit", "--allow-empty", "-m", "direct fix")
    assert not check_ref(repo, "developer", base=base)[0]


def test_pr_checks_synthetic_merge_and_release_history(tmp_path):
    from nexgen_core.lanes import check_ref
    repo = _repo_with_lanes(tmp_path)
    base = _git(repo, "rev-parse", "developer").stdout.strip()
    _git(repo, "checkout", "-qb", "dev/x", "developer")
    _git(repo, "commit", "--allow-empty", "-m", "lane change")
    _git(repo, "checkout", "-qb", "pr-merge", "developer")
    _git(repo, "merge", "--no-ff", "dev/x", "-m", "synthetic merge")
    assert check_ref(repo, "developer", tip="HEAD", base=base)[0]
    assert not check_ref(repo, "release/v9", tip="HEAD")[0]


def test_missing_integration_ref_is_unknown_in_doctor(tmp_path):
    repo = _repo_with_lanes(tmp_path)
    _git(repo, 'checkout', '-qb', 'release/unknown')
    _git(repo, 'branch', '-D', 'developer')
    outcome = check_engine_lane(repo)
    assert outcome.severity == Severity.UNDETERMINED


def test_first_developer_push_checks_merges_from_published_base(tmp_path):
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _git(repo, "checkout", "-qb", "dev/first", "developer")
    _git(repo, "commit", "--allow-empty", "-m", "lane work")
    _git(repo, "checkout", "developer")
    _git(repo, "merge", "--no-ff", "dev/first", "-m", "merge first lane")
    command = [
        sys.executable, str(SCRIPTS_DIR / "lane_guard.py"),
        "--repo", str(repo), "--ref", "developer", "--tip", "HEAD",
        "--base", "0" * 40, "--first-push-base", "origin/main",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    _git(repo, "commit", "--allow-empty", "-m", "direct fix")
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1
    assert "direct commit on developer" in result.stderr


def test_first_push_base_cannot_override_an_existing_tip(tmp_path):
    from nexgen_core.lanes import check_ref
    repo = _repo_with_lanes(tmp_path)
    base = _git(repo, "rev-parse", "developer").stdout.strip()
    _git(repo, "checkout", "developer")
    _git(repo, "commit", "--allow-empty", "-m", "direct fix")
    assert not check_ref(repo, "developer", base="0" * 40)[0]
    assert not check_ref(repo, "developer", base="0" * 40, first_push_base="missing")[0]
    assert not check_ref(repo, "developer", base=base, first_push_base="HEAD")[0]
