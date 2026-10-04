"""History, fresh-clone and doctor cases for the developer/main contract."""
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


def test_unknown_refs_fail_closed(tmp_path):
    lg = _load_guard()
    repo = _repo_with_lanes(tmp_path)
    assert not lg.check_ref(repo, "developer", tip="missing", base="main")[0]
    assert not lg.check_ref(repo, "developer", base="missing")[0]
    assert lg.guarded_ref("main")
    assert not lg.guarded_ref("developer")


def test_first_developer_push_requires_a_trusted_published_base(tmp_path):
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _git(repo, "checkout", "developer")
    _git(repo, "commit", "--allow-empty", "-m", "development")
    lg = _load_guard()
    assert not lg.check_ref(repo, "developer", base="0" * 40)[0]
    assert not lg.check_ref(repo, "developer", base="0" * 40, first_push_base="missing")[0]
    assert lg.check_ref(repo, "developer", base="0" * 40, first_push_base="origin/main")[0]


def test_first_push_base_cannot_override_an_existing_tip(tmp_path):
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "checkout", "developer")
    _git(repo, "commit", "--allow-empty", "-m", "published development")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")
    lg = _load_guard()
    assert not lg.check_ref(repo, "developer", tip="main", base=base, first_push_base="main")[0]


def test_main_merge_from_an_unintegrated_branch_is_refused(tmp_path):
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _git(repo, "checkout", "-b", "outside")
    _git(repo, "commit", "--allow-empty", "-m", "unintegrated work")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "outside", "-m", "release: misleading label")
    assert not _load_guard().check_ref(repo, "main")[0]


def test_main_direct_release_chore_is_still_refused(tmp_path):
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _git(repo, "commit", "--allow-empty", "-m", "release: misleading direct commit")
    assert not _load_guard().check_ref(repo, "main")[0]


def test_remote_only_developer_ref_works_in_ci_clone(tmp_path):
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "checkout", "developer")
    _git(repo, "commit", "--allow-empty", "-m", "release: ready")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "developer", "-m", "release merge")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(repo), str(clone)], check=True, capture_output=True)
    previous = _git(repo, "rev-parse", "main^").stdout.strip()
    assert _load_guard().check_ref(clone, "main", base=previous)[0]


def test_doctor_warns_on_retired_branch_and_detached_work(tmp_path):
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "checkout", "-b", "dev/old")
    assert check_engine_lane(repo).severity == Severity.WARN
    _git(repo, "checkout", "--detach")
    assert check_engine_lane(repo).severity == Severity.WARN
    assert check_engine_lane(tmp_path / "absent") is None


def test_doctor_reports_missing_remote_as_unknown(tmp_path):
    repo = _repo_with_lanes(tmp_path)
    _git(repo, "checkout", "developer")
    assert check_engine_lane(repo).severity == Severity.UNDETERMINED
