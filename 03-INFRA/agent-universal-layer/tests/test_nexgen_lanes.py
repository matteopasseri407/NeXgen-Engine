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
    assert lg.guarded_ref("developer") is False
    assert lg.guarded_ref("dev/engine") is False


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
