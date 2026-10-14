"""`release.py ship`: the release trafila in one command, driven against a scripted git and gh."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.release_ship import Shipper, ShipStop  # noqa: E402

RELEASE_HEAD = "a" * 40
MERGED = "b" * 40
GREEN_ROLLUP = [
    {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"name": "lane-guard", "status": "COMPLETED", "conclusion": "FAILURE"},
    {"context": "leak-scan", "state": "SUCCESS"},
]


class FakeWorld:
    """Answers git and gh the way GitHub would for one release, and records every call."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls: list[tuple[str, ...]] = []
        self.rollups = [[], GREEN_ROLLUP]
        self.release_runs = [[{"databaseId": 7, "status": "completed", "conclusion": "success", "headBranch": "v2.14.0"}]]
        self.trust_ok = True
        self.synced_after = 1
        self.open_prs: list[dict] = []

    def __call__(self, argv, cwd):
        argv = tuple(str(a) for a in argv)
        self.calls.append(argv)
        out, code = self.answer(argv)
        return subprocess.CompletedProcess(argv, code, out, "" if code == 0 else "boom")

    def answer(self, argv):  # noqa: C901 - a dispatch table reads best flat
        if argv[-1] == "preflight":
            return "Ready to tag.", 0
        if "release_trust.py" in " ".join(argv):
            return ("v2.14.0: verified", 0) if self.trust_ok else ("v2.14.0: untrusted: key not pinned", 1)
        match argv[:3]:
            case ("git", "rev-parse", "--abbrev-ref"):
                return "developer", 0
            case ("git", "status", _):
                return "", 0
            case ("git", "tag", "--list"):
                return "v2.13.0\nv2.12.1", 0
            case ("git", "rev-parse", "HEAD"):
                return RELEASE_HEAD, 0
            case ("git", "rev-parse", "origin/main"):
                return MERGED, 0
            case ("git", "merge-base", "--is-ancestor"):
                if argv[-1] == "origin/developer":
                    self.synced_after -= 1
                    return "", 0 if self.synced_after < 0 else 1
                return "", 0
            case ("gh", "pr", "list"):
                return json.dumps(self.open_prs), 0
            case ("gh", "pr", "create"):
                return "https://github.com/o/r/pull/114", 0
            case ("gh", "pr", "view"):
                rollup = self.rollups.pop(0) if len(self.rollups) > 1 else self.rollups[0]
                return json.dumps({"headRefOid": RELEASE_HEAD, "statusCheckRollup": rollup}), 0
            case ("gh", "run", "list") if "ci.yml" in argv:
                return json.dumps([{"databaseId": 5, "status": "completed", "conclusion": "success"}]), 0
            case ("gh", "run", "list"):
                runs = self.release_runs.pop(0) if len(self.release_runs) > 1 else self.release_runs[0]
                return json.dumps(runs), 0
            case ("gh", "release", "view"):
                return json.dumps({"isDraft": False, "assets": [1, 2, 3]}), 0
        return "", 0


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "VERSION").write_text("2.13.0\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n- shipped by one command\n\n## [2.13.0] - 2026-10-14\n", encoding="utf-8")
    return tmp_path


def _shipper(repo, world, **kw):
    return Shipper(repo=repo, engine_root=repo / "engine", run=world, sleep=lambda _s: None, log=lambda _m: None, poll_seconds=1, **kw)


def test_a_release_goes_from_developer_to_a_synced_tag(repo):
    world = FakeWorld(repo)
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert (repo / "VERSION").read_text(encoding="utf-8") == "2.14.0\n"
    steps = [c[:3] for c in world.calls]
    order = [("git", "commit", "-q"), ("gh", "pr", "create"), ("gh", "pr", "merge"), ("git", "tag", "-s"), ("gh", "release", "view")]
    assert [steps.index(s) for s in order] == sorted(steps.index(s) for s in order)
    pushed_tag = [c for c in world.calls if c[:2] == ("git", "push") and c[-1] == "v2.14.0"]
    assert pushed_tag, "the tag must be pushed"


def test_the_ignored_lane_guard_does_not_stop_it_but_a_real_failure_does(repo):
    world = FakeWorld(repo)
    world.rollups = [[{"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}]]
    with pytest.raises(ShipStop, match="failing checks: test"):
        _shipper(repo, world).ship("minor")
    assert not any(c[:3] == ("gh", "pr", "merge") for c in world.calls)


def test_a_tag_the_installed_copies_would_refuse_is_deleted_and_never_pushed(repo):
    world = FakeWorld(repo)
    world.trust_ok = False
    with pytest.raises(ShipStop, match="would refuse"):
        _shipper(repo, world).ship("minor")
    assert ("git", "tag", "-d", "v2.14.0") in world.calls
    assert not any(c[:2] == ("git", "push") and c[-1] == "v2.14.0" for c in world.calls)


def test_a_failed_release_run_is_rerun_once(repo):
    world = FakeWorld(repo)
    failed = [{"databaseId": 7, "status": "completed", "conclusion": "failure", "headBranch": "v2.14.0"}]
    ok = [{**failed[0], "conclusion": "success"}]
    world.release_runs = [failed, ok]
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert ("gh", "run", "rerun", "7") in world.calls


def test_running_it_again_after_a_stop_does_not_bump_twice(repo):
    (repo / "VERSION").write_text("2.14.0\n", encoding="utf-8")
    world = FakeWorld(repo)
    world.open_prs = [{"number": 114}]
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert not any(c[:2] == ("git", "commit") for c in world.calls)
    assert not any(c[:3] == ("gh", "pr", "create") for c in world.calls)


def test_it_refuses_to_start_from_a_dirty_tree(repo):
    world = FakeWorld(repo)
    original = world.answer
    world.answer = lambda argv: (" M x.py", 0) if argv[:2] == ("git", "status") else original(argv)
    with pytest.raises(ShipStop, match="uncommitted"):
        _shipper(repo, world).ship("minor")


def test_waiting_has_a_limit(repo):
    world = FakeWorld(repo)
    world.rollups = [[]]
    with pytest.raises(ShipStop, match="gave up waiting"):
        _shipper(repo, world, wait_limit_seconds=3).ship("minor")


def test_install_is_checked_against_the_installed_version(repo):
    world = FakeWorld(repo)
    installed = repo / "installed"
    (installed / "03-INFRA").mkdir(parents=True)
    (installed / "VERSION").write_text("2.13.0\n", encoding="utf-8")
    shipper = _shipper(repo, world)
    shipper.engine_root = installed / "03-INFRA"
    with pytest.raises(ShipStop, match="still on 2.13.0"):
        shipper.ship("minor", install=True)
    (installed / "VERSION").write_text("2.14.0\n", encoding="utf-8")
    world.synced_after = 1
    assert shipper.ship("minor", install=True) == "v2.14.0"


def test_an_update_rolled_back_by_a_colliding_timer_is_retried_once(repo, tmp_path):
    installed = repo / "installed"
    (installed / "03-INFRA").mkdir(parents=True)
    (installed / "VERSION").write_text("2.13.0\n", encoding="utf-8")
    world = FakeWorld(repo)
    updates = iter([1, 0])
    original = world.answer

    def answer(argv):
        if argv[:2] == ("nexgen", "update"):
            code = next(updates)
            if code == 0:
                (installed / "VERSION").write_text("2.14.0\n", encoding="utf-8")
            return "Rolled back to v2.13.0." if code else "installed", code
        return original(argv)

    world.answer = answer
    shipper = _shipper(repo, world, install_log=tmp_path / "install.log")
    shipper.engine_root = installed / "03-INFRA"
    assert shipper.ship("minor", install=True) == "v2.14.0"
    assert "Rolled back" in (tmp_path / "install.log").read_text(encoding="utf-8")
    assert sum(1 for c in world.calls if c[:2] == ("nexgen", "update")) == 2
