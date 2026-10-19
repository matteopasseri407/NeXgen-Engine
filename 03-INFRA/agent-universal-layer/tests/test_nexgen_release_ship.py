"""`release.py ship`: the release trafila in one command, driven against a scripted git and gh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.release_ship import SMOKE_CONTEXT, Shipper, ShipStop  # noqa: E402

RELEASE_HEAD = "a" * 40
MERGED = "b" * 40
GREEN_ROLLUP = [
    {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"name": "lane-guard", "status": "COMPLETED", "conclusion": "FAILURE"},
    {"context": "leak-scan", "state": "SUCCESS"},
]


def command_name(argv) -> str:
    """The command as the machine invokes it: a launcher's path is the launcher's own name.

    The install talks to `~/.local/bin/nexgen.cmd` (`.cmd` on Windows only) where it
    used to call a bare `nexgen`, and this world answers by command, not by path.
    """
    return Path(argv[0]).stem


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
        self.already_merged = False
        self.main_carries_the_branch = False
        self.developer_synced = True
        #: What `main`'s branch protection requires. `engine-tests-windows` is not
        #: on it: it is slow and flaky, and CI reports it without blocking a release.
        self.required_checks = ["test"]
        self.check_runs: dict[str, list[dict]] = {}
        self.ci_on_main = "success"
        self.smoke_fails = False
        self.installed_smoke_fails = False
        #: Where a successful `nexgen update` writes the version it installed.
        self.install_root: Path | None = None
        #: What the updater reports and refuses with, word for word.
        self.data_repo: Path | None = None
        self.data_dirty = False
        self.update_refusal = ""
        self.update_noop = False
        #: What this machine and the remote have as tags, for a rerun after an interruption.
        self.local_tags = ["v2.13.0", "v2.12.1"]
        self.local_tag_commits: dict[str, str] = {}
        self.remote_tags: list[str] = []
        #: The tag this run is publishing, so the release workflow's run is reported for it.
        self.shipping_tag = "v2.14.0"
        #: The tree GitHub tested for the pull request, and the tree its merge carries here.
        self.tested_tree = "a" * 40
        self.merge_tree = "a" * 40
        self.pull_request_sha = "d" * 40
        #: Every commit status ship published, as the argv it published it with.
        self.statuses: list[tuple[str, ...]] = []

    def __call__(self, argv, cwd):
        argv = tuple(str(a) for a in argv)
        self.calls.append(argv)
        out, code = self.answer((command_name(argv), *argv[1:]))
        return subprocess.CompletedProcess(argv, code, out, "" if code == 0 else "boom")

    def launches(self, *words: str) -> list[tuple[str, ...]]:
        """Every recorded call whose command and arguments are exactly `words` (`nexgen update`)."""
        return [call for call in self.calls if (command_name(call), *call[1:len(words)]) == words]

    def api(self, url: str) -> str:
        """GitHub's api answers: the required checks (from both places GitHub keeps them), and a commit's runs."""
        if url.endswith("required_status_checks"):
            return json.dumps({"contexts": self.required_checks, "checks": []})
        if url.endswith("/rules/branches/main"):
            rule = {"type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": name} for name in self.required_checks]}}
            return json.dumps([rule, {"type": "non_fast_forward"}])
        if "/git/ref/pull/" in url:
            return json.dumps({"object": {"sha": self.pull_request_sha}})
        if url.endswith(self.pull_request_sha):
            return json.dumps({"commit": {"tree": {"sha": self.tested_tree}}})
        sha = url.split("/")[-2]
        runs = self.check_runs.get(sha)
        if runs is None:
            runs = [{"name": name, "status": "completed", "conclusion": "success"} for name in self.required_checks]
        return json.dumps({"check_runs": runs})

    def answer(self, argv):  # noqa: C901 - a dispatch table reads best flat
        direct = self._direct(argv)
        return self._table(argv) if direct is None else direct

    def _direct(self, argv):
        """The answers that need the whole command line, not just its first three words."""
        if argv[:3] == ("git", "tag", "-s"):
            self.shipping_tag = argv[3]
        if argv[:2] == ("git", "push") and argv[-1].startswith("v"):
            self.shipping_tag = argv[-1]
        if argv[-1].endswith("smoke.py"):
            installed = self.install_root is not None and str(self.install_root) in argv[-1]
            failing = self.installed_smoke_fails if installed else self.smoke_fails
            return ("SMOKE FAILED (1)", 1) if failing else ("SMOKE OK (12s)", 0)
        if argv[:2] == ("nexgen", "update"):
            return self._update(argv)
        if argv[:2] == ("git", "-C") and "status" in argv:
            return ("?? notes.md\n" if self.data_dirty else ""), 0
        if argv[:2] == ("gh", "api") and "/statuses/" in " ".join(argv):
            self.statuses.append(argv)
            return "", 0
        if argv[:2] == ("gh", "api"):
            url = next(arg for arg in argv[2:] if arg.startswith("repos/"))
            return self.api(url), 0
        if argv[:3] == ("git", "ls-remote", "--tags"):
            tag = argv[-1].removeprefix("refs/tags/")
            self.shipping_tag = tag  # the tag ship is working on is the release being published
            return ((("c" * 40) + "\t" + argv[-1] + "\n") if tag in self.remote_tags else ""), 0
        if argv[:3] == ("git", "rev-parse", "--verify"):
            tag = argv[-1].removeprefix("refs/tags/").removesuffix("^{commit}")
            return ((self.local_tag_commits.get(tag) or MERGED) + "\n"), 0 if tag in self.local_tags else 1
        if argv[-1] == "preflight":
            return "Ready to tag.", 0
        if "release_trust.py" in " ".join(argv):
            return ("v2.14.0: verified", 0) if self.trust_ok else ("v2.14.0: untrusted: key not pinned", 1)
        return None

    def _update(self, argv):
        """What `nexgen update` prints, and the version it installs when it installs one."""
        folder = self.install_root or Path("?")
        report = f"Engine: {folder / '03-INFRA'}\nData:   {self.data_repo or folder}\n"
        if self.update_refusal:
            return report + f"nexgen-update: ERROR: {self.update_refusal}", 1
        if self.update_noop:
            return report + "NeXGen Engine is already at the requested released version.", 0
        if self.install_root is not None:
            (self.install_root / "VERSION").write_text(argv[-1] + "\n", encoding="utf-8")
        return report + f"installed {argv[-1]}", 0

    def _table(self, argv):
        """The short commands, by their first three words."""
        match argv[:3]:
            case ("git", "rev-parse", "--abbrev-ref"):
                return "developer", 0
            case ("git", "status", _):
                return "", 0
            case ("git", "diff", "--quiet", *_):
                return "", 0 if self.main_carries_the_branch else 1
            case ("git", "tag", "--list"):
                return "\n".join(self.local_tags), 0
            case ("git", "rev-parse", "HEAD"):
                return RELEASE_HEAD, 0
            case ("git", "rev-parse", _) if argv[-1].endswith("^{tree}"):
                return self.merge_tree, 0
            case ("git", "rev-parse", "origin/main"):
                return MERGED, 0
            case ("git", "merge-base", "--is-ancestor"):
                if argv[-1] == "origin/main":
                    return "", 0 if self.already_merged else 1
                if argv[-2:] == ("origin/main", "origin/developer"):
                    return "", 0 if self.developer_synced else 1
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
                return json.dumps([{"databaseId": 5, "status": "completed", "conclusion": self.ci_on_main}]), 0
            case ("gh", "run", "list"):
                runs = self.release_runs.pop(0) if len(self.release_runs) > 1 else self.release_runs[0]
                return json.dumps([{**run, "headBranch": self.shipping_tag} for run in runs]), 0
            case ("gh", "release", "view"):
                return json.dumps({"isDraft": False, "assets": [1, 2, 3]}), 0
            case ("nexgen", "--version"):
                # A real machine's launcher answers with its own version banner, and the
                # install reads the channel from it; a world that stayed silent here was
                # standing in for a launcher that cannot start.
                installed = self.install_root if self.install_root is not None else self.repo
                version = (installed / "VERSION").read_text(encoding="utf-8").strip()
                return f"NeXgen Engine {version} (stable)", 0
        return "", 0


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "VERSION").write_text("2.13.0\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n- shipped by one command\n\n## [2.13.0] - 2026-10-14\n", encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def installed_launcher(tmp_path, monkeypatch):
    """A machine whose command is installed where the installer writes it.

    The install step calls that path and never the `nexgen` on PATH, so every test
    that installs needs one to exist. Returns it, so a test can say which one ran.
    """
    from nexgen_core import release_install
    from nexgen_core.module_install import shim_path
    from nexgen_core.shims import PRIMARY

    home = tmp_path / "home"
    launcher = shim_path(home / ".local" / "bin", PRIMARY)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("# the installed command\n", encoding="utf-8")
    monkeypatch.setattr(release_install, "resolve_home", lambda _home=None: home)
    return launcher


def installed_engine(repo: Path, version: str, *, smoke: bool = True) -> Path:
    """An installed engine as the updater leaves it: a release tree, no `.venv` of its own."""
    installed = repo / "installed"
    (installed / "03-INFRA" / "scripts").mkdir(parents=True, exist_ok=True)
    (installed / "VERSION").write_text(version + "\n", encoding="utf-8")
    if smoke:
        (installed / "03-INFRA" / "scripts" / "smoke.py").write_text("# the installed smoke\n", encoding="utf-8")
    return installed


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


def test_a_non_required_job_that_failed_does_not_hold_the_release(repo):
    """`engine-tests-windows` is not required on main: CI reports it, and a release
    does not wait for it. The whole run's verdict used to decide."""
    world = FakeWorld(repo)
    world.rollups = [[
        {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "engine-tests-windows", "status": "COMPLETED", "conclusion": "FAILURE"},
    ]]
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert any(c[:3] == ("gh", "pr", "merge") for c in world.calls)


def test_a_non_required_job_still_queued_does_not_hold_the_release(repo):
    world = FakeWorld(repo)
    world.rollups = [[
        {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "engine-tests-windows", "status": "IN_PROGRESS"},
    ]]
    assert _shipper(repo, world, wait_limit_seconds=3).ship("minor") == "v2.14.0"
    assert any(c[:3] == ("gh", "pr", "merge") for c in world.calls)


def test_the_verdict_reads_the_required_checks_from_the_branch_protection(repo):
    world = FakeWorld(repo)
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert ("gh", "api", "repos/{owner}/{repo}/branches/main/protection/required_status_checks") in world.calls
    assert not any("ci.yml" in c for c in world.calls), "the workflow run is the fallback, not the rule"


def test_the_tag_carries_the_checks_the_release_was_verified_against(repo):
    """release.yml reads the required list from the tag's annotation: its own token
    cannot read main's branch protection, and the list travels signed with the tag."""
    world = FakeWorld(repo)
    world.required_checks = ["test", "leak-scan"]
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    tag_call = next(call for call in world.calls if call[:3] == ("git", "tag", "-s"))
    messages = [tag_call[index + 1] for index, arg in enumerate(tag_call) if arg == "-m"]
    assert any(message.startswith("Release v2.14.0") for message in messages)
    assert "Required checks: leak-scan, test" in messages


def test_the_required_checks_are_read_from_both_shapes_of_the_api(repo):
    world = FakeWorld(repo)
    original = world.answer

    def answer(argv):
        if argv[:2] == ("gh", "api") and argv[-1].endswith("required_status_checks"):
            return json.dumps({"contexts": ["test"], "checks": [{"context": "leak-scan"}]}), 0
        return original(argv)

    world.answer = answer
    assert _shipper(repo, world)._required_checks() == {"test", "leak-scan"}


def test_an_unreadable_protection_falls_back_to_every_check(repo):
    """A repository without branch protection, or a token that cannot read it, keeps
    the strict reading: every check the run reports counts."""
    world = FakeWorld(repo)
    original = world.answer

    def answer(argv):
        if argv[:2] == ("gh", "api") and "statuses/" not in " ".join(argv):
            return "", 1
        return original(argv)

    world.answer = answer
    assert _shipper(repo, world)._required_checks() is None
    world.rollups = [[{"name": "engine-tests-windows", "status": "COMPLETED", "conclusion": "FAILURE"}]]
    with pytest.raises(ShipStop, match="failing checks: engine-tests-windows"):
        _shipper(repo, world).ship("minor")


def test_a_red_non_required_job_on_main_does_not_stop_the_tag(repo):
    world = FakeWorld(repo)
    world.merge_tree = "c" * 40  # a different tree on main: the required checks are asked for
    world.ci_on_main = "failure"  # the run's verdict is red because of a job nobody required
    world.check_runs = {MERGED: [
        {"name": "test", "status": "completed", "conclusion": "success"},
        {"name": "engine-tests-windows", "status": "completed", "conclusion": "failure"},
    ]}
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert any(c[:2] == ("git", "push") and c[-1] == "v2.14.0" for c in world.calls)


def test_a_red_required_check_on_main_stops_before_the_tag(repo):
    world = FakeWorld(repo)
    world.merge_tree = "c" * 40  # a different tree on main: the required checks are asked for
    world.ci_on_main = "failure"
    world.check_runs = {MERGED: [{"name": "test", "status": "completed", "conclusion": "failure"}]}
    with pytest.raises(ShipStop, match="failing required checks on main: test"):
        _shipper(repo, world).ship("minor")
    assert not any(c[:2] == ("git", "push") and c[-1] == "v2.14.0" for c in world.calls)


def test_the_local_smoke_is_recorded_on_the_pull_request_before_the_merge(repo):
    """The checks on GitHub know nothing about this machine; the smoke is the proof they
    cannot give, run while they run and published on the pull request's head."""
    world = FakeWorld(repo)
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    smoke = [call for call in world.calls if call[-1].endswith("smoke.py")]
    assert smoke and str(repo / "03-INFRA" / "scripts" / "smoke.py") in smoke[0]
    assert smoke[0][0] == sys.executable
    published = world.statuses[0]
    assert f"repos/{{owner}}/{{repo}}/statuses/{RELEASE_HEAD}" in published
    # smoke-windows on the Windows desktop, smoke-local on any other machine.
    assert "state=success" in published and f"context={SMOKE_CONTEXT}" in published
    assert any(arg.startswith("description=passed in ") and arg.endswith("s on this machine") for arg in published)
    assert world.calls.index(smoke[0]) < world.calls.index(next(c for c in world.calls if c[:3] == ("gh", "pr", "merge")))


def test_a_failing_local_smoke_is_recorded_and_nothing_is_merged(repo):
    world = FakeWorld(repo)
    world.smoke_fails = True
    with pytest.raises(ShipStop, match="smoke on this machine failed"):
        _shipper(repo, world).ship("minor")
    assert "state=failure" in world.statuses[0]
    assert not any(c[:3] == ("gh", "pr", "merge") for c in world.calls)


def test_the_merge_that_carries_the_tested_tree_skips_the_wait_on_main(repo):
    """GitHub tested the pull request's merge ref. When the merge on main carries that same
    tree, `pr merge` produced what was tested and there is no second CI to wait for."""
    world = FakeWorld(repo)
    world.ci_on_main = "failure"  # the run on main is red: nobody waits for it
    world.check_runs = {MERGED: [{"name": "test", "status": "completed", "conclusion": "failure"}]}
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert any(c[:2] == ("git", "push") and c[-1] == "v2.14.0" for c in world.calls)
    assert not any("/check-runs" in " ".join(c) for c in world.calls), "the checks on main are not even asked for"
    # The release workflow judges the tagged commit: this machine's proof is recorded there too.
    assert any(f"statuses/{MERGED}" in " ".join(status) and "state=success" in status for status in world.statuses)


def test_a_merge_that_carries_another_tree_waits_for_the_checks_on_main(repo):
    world = FakeWorld(repo)
    world.merge_tree = "c" * 40  # main moved, or the merge came out differently
    world.check_runs = {MERGED: [{"name": "test", "status": "completed", "conclusion": "failure"}]}
    with pytest.raises(ShipStop, match="failing required checks on main: test"):
        _shipper(repo, world).ship("minor")
    assert not any(c[:2] == ("git", "push") and c[-1] == "v2.14.0" for c in world.calls)


def test_an_unreadable_pull_request_ref_waits_for_the_checks_on_main(repo):
    """No merge ref to compare with (the API will not say): waiting is the safe reading."""
    world = FakeWorld(repo)
    original = world.answer

    def answer(argv):
        if argv[:2] == ("gh", "api") and "/git/ref/pull/" in " ".join(argv):
            return "", 1
        return original(argv)

    world.answer = answer
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert any("/check-runs" in " ".join(c) for c in world.calls)


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
    installed = installed_engine(repo, "2.13.0")
    shipper = _shipper(repo, world)
    shipper.engine_root = installed / "03-INFRA"
    with pytest.raises(ShipStop, match="still on 2.13.0"):
        shipper.ship("minor", install=True)
    (installed / "VERSION").write_text("2.14.0\n", encoding="utf-8")
    world.synced_after = 1
    assert shipper.ship("minor", install=True) == "v2.14.0"


def test_the_install_runs_the_installed_engines_own_smoke(repo):
    """The release is about the engine the machine runs: the smoke is the installed
    tree's own script, run with the interpreter the launchers use, not this checkout's."""
    world = FakeWorld(repo)
    installed = installed_engine(repo, "2.13.0")
    world.install_root = installed
    shipper = _shipper(repo, world)
    shipper.engine_root = installed / "03-INFRA"
    assert shipper.ship("minor", install=True) == "v2.14.0"
    smoke = [call for call in world.calls if call[-1] == str(installed / "03-INFRA" / "scripts" / "smoke.py")]
    assert smoke, "the installed engine's own smoke must run"
    assert Path(smoke[0][0]).name.lower().startswith(("python", "py"))


def test_an_installed_engine_without_a_smoke_cannot_be_called_proved(repo):
    world = FakeWorld(repo)
    installed = installed_engine(repo, "2.13.0", smoke=False)
    world.install_root = installed
    shipper = _shipper(repo, world)
    shipper.engine_root = installed / "03-INFRA"
    with pytest.raises(ShipStop, match="has no smoke.py"):
        shipper.ship("minor", install=True)


def test_a_failed_smoke_puts_the_previous_version_back_and_stops(repo, tmp_path):
    world = FakeWorld(repo)
    world.installed_smoke_fails = True
    installed = installed_engine(repo, "2.13.0")
    world.install_root = installed
    shipper = _shipper(repo, world, install_log=tmp_path / "install.log")
    shipper.engine_root = installed / "03-INFRA"
    with pytest.raises(ShipStop, match="back on 2.13.0"):
        shipper.ship("minor", install=True)
    assert world.launches("nexgen", "update", "--yes", "--target", "2.13.0")
    assert "SMOKE FAILED" in (tmp_path / "install.log").read_text(encoding="utf-8")


def test_a_rollback_that_does_not_finish_says_what_to_run_by_hand(repo, tmp_path):
    world = FakeWorld(repo)
    world.installed_smoke_fails = True
    installed = installed_engine(repo, "2.13.0")
    world.install_root = installed
    original = world.answer

    def answer(argv):
        if argv[:2] == ("nexgen", "update") and argv[-1] == "2.13.0":
            return "the rollback could not start", 1
        return original(argv)

    world.answer = answer
    shipper = _shipper(repo, world, install_log=tmp_path / "install.log")
    shipper.engine_root = installed / "03-INFRA"
    with pytest.raises(ShipStop, match="by hand"):
        shipper.ship("minor", install=True)


def _installer(repo, world, **kw):
    """The machine half of a release, on the same scripted world."""
    from nexgen_core.release_install import Installer

    return Installer(engine_root=repo / "installed" / "03-INFRA", repo=repo, run=world,
                     log=lambda _m: None, sleep=lambda _s: None, poll_seconds=1, **kw)


def test_the_launcher_interpreter_is_the_provisioned_runtime(repo, tmp_path, monkeypatch):
    """A release tree may have no `.venv`: the installed engine runs on its runtime, and
    the interpreter never comes from inside the engine folder."""
    from nexgen_core import release_install

    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(release_install, "resolve_runtime_dir", lambda home=None: runtime)
    installer = _installer(repo, FakeWorld(repo))
    (runtime / ".provisioned").write_text("", encoding="utf-8")
    assert installer.launcher_python() == [str(runtime / "bin" / "python")]
    (runtime / ".provisioned").unlink()
    chosen = installer.launcher_python()
    assert Path(chosen[0]).name.lower().startswith(("python", "py"))
    assert installer.engine_root not in Path(chosen[0]).parents


def test_the_version_probe_is_the_launchers_own(repo):
    """The launchers accept a Python 3.11 or newer; the interpreter for the installed
    smoke is chosen the same way, so a stub that is not Python is never picked."""
    from nexgen_core import release_install, release_ship

    installer = release_install.Installer(engine_root=repo / "engine", repo=repo,
                                          run=release_ship._default_run, log=lambda _m: None)
    assert installer._runs_this_engine([sys.executable])
    assert not installer._runs_this_engine([shutil.which("git") or "git"])


def test_the_install_uses_the_installed_launcher_not_the_one_on_path(repo, tmp_path, monkeypatch, installed_launcher):
    """Ship runs in a development checkout, whose own `nexgen` is what PATH resolves.

    Started from there, that command answers "already at the requested released
    version" about the checkout: v2.16.3 updated nothing and left this machine on
    2.16.2 with the release green. The update and the rollback go through the
    installed launcher, which runs the engine this machine actually has.
    """
    world = FakeWorld(repo)
    world.install_root = installed_engine(repo, "2.13.0")
    checkout_bin = tmp_path / "checkout" / "bin"
    checkout_bin.mkdir(parents=True)
    (checkout_bin / installed_launcher.name).write_text(
        "NeXGen Engine is already at the requested released version.\n", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{checkout_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    _installer(repo, world).install("2.14.0")

    updates = world.launches("nexgen", "update")
    assert updates, "the install must run the update"
    assert {call[0] for call in updates} == {str(installed_launcher)}
    assert all(call[0] != "nexgen" for call in world.calls), "nothing goes through the checkout's own command"


def test_a_machine_without_its_launcher_stops_the_install(repo, tmp_path, monkeypatch):
    """There is nothing here that runs the installed engine: ship says so and stops,
    rather than falling back to the `nexgen` on PATH and updating another tree."""
    from nexgen_core import release_install
    from nexgen_core.release_install import InstallProblem

    monkeypatch.setattr(release_install, "resolve_home", lambda _home=None: tmp_path / "empty-home")
    world = FakeWorld(repo)
    world.install_root = installed_engine(repo, "2.13.0")
    with pytest.raises(InstallProblem, match="installed command"):
        _installer(repo, world).install("2.14.0")
    assert not world.launches("nexgen", "update")


@pytest.mark.parametrize("banner,code", [
    ("", 0),                                            # a launcher that starts but says nothing
    ("NeXgen Engine 2.13.0", 0),                        # an older banner without the channel
    ("Traceback (most recent call last): boom", 1),     # a launcher that does not start
])
def test_an_unreadable_channel_banner_stops_the_install_instead_of_guessing_stable(
    repo, tmp_path, banner, code
):
    """Guessing "stable" added `--target <tag>`, which a nightly or beta machine refuses:
    the release then stopped two minutes later blaming an update nothing had read."""
    from nexgen_core.release_install import InstallProblem

    world = FakeWorld(repo)
    world.install_root = installed_engine(repo, "2.13.0")
    original = world.answer
    world.answer = lambda argv: (banner, code) if argv[:2] == ("nexgen", "--version") else original(argv)

    with pytest.raises(InstallProblem, match="did not report its channel"):
        _installer(repo, world, install_log=tmp_path / "install.log").install("2.14.0")

    assert not world.launches("nexgen", "update")


def test_an_update_refused_for_a_dirty_vault_says_which_folder_and_stops(repo, tmp_path):
    """The updater refuses before it moves the engine. That is not a rollback to retry:
    it is one line naming the folder that has to be committed or stashed."""
    world = FakeWorld(repo)
    installed = installed_engine(repo, "2.13.0")
    world.install_root = installed
    world.data_repo = repo / "vault"
    world.update_refusal = "data repository is dirty; commit or stash your work yourself before updating:\n?? notes.md"
    shipper = _shipper(repo, world, install_log=tmp_path / "install.log")
    shipper.engine_root = installed / "03-INFRA"
    with pytest.raises(ShipStop) as stop:
        shipper.ship("minor", install=True)
    assert str(repo / "vault") in str(stop.value)
    assert "uncommitted or untracked" in str(stop.value)
    assert "rolled back" not in str(stop.value)
    assert len(world.launches("nexgen", "update")) == 1


def test_an_install_already_at_the_version_still_reports_a_dirty_vault(repo, tmp_path):
    """`nexgen update` returns "already at the requested released version" before it
    looks at the vault, and the release ended green on a machine whose vault cannot be
    aligned. Ship asks the same question itself and says which folder."""
    world = FakeWorld(repo)
    installed = installed_engine(repo, "2.14.0")
    vault = repo / "vault"
    vault.mkdir()
    world.install_root = installed
    world.data_repo = vault
    world.update_noop = True
    world.data_dirty = True
    shipper = _shipper(repo, world, install_log=tmp_path / "install.log")
    shipper.engine_root = installed / "03-INFRA"
    with pytest.raises(ShipStop, match="uncommitted or untracked") as stop:
        shipper.ship("minor", install=True)
    assert str(vault) in str(stop.value)
    assert ("git", "-C", str(vault), "status", "--porcelain=v1", "--untracked-files=normal") in world.calls


def test_a_clean_vault_does_not_stop_the_install(repo, tmp_path):
    world = FakeWorld(repo)
    installed = installed_engine(repo, "2.13.0")
    vault = repo / "vault"
    vault.mkdir()
    world.install_root = installed
    world.data_repo = vault
    shipper = _shipper(repo, world, install_log=tmp_path / "install.log")
    shipper.engine_root = installed / "03-INFRA"
    assert shipper.ship("minor", install=True) == "v2.14.0"


def test_an_update_rolled_back_by_a_colliding_timer_is_retried_once(repo, tmp_path):
    installed = installed_engine(repo, "2.13.0")
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
    assert len(world.launches("nexgen", "update")) == 2


def test_a_rerun_after_the_merge_does_not_open_a_second_pull_request(repo):
    (repo / "VERSION").write_text("2.14.0\n", encoding="utf-8")
    world = FakeWorld(repo)
    world.already_merged = True
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert not any(c[:3] in {("gh", "pr", "create"), ("gh", "pr", "merge"), ("gh", "pr", "list")} for c in world.calls)
    assert any(c[:3] == ("git", "tag", "-s") for c in world.calls)


def test_a_branch_main_already_carries_skips_the_pull_request(repo):
    """After sync-developer merged main back, developer is 'ahead' only by that merge:
    a pull request would change no file, GitHub opens it anyway, and the release
    looped on empty pull requests (#124, #125 on 2026-10-17)."""
    (repo / "VERSION").write_text("2.14.0\n", encoding="utf-8")
    world = FakeWorld(repo)
    world.main_carries_the_branch = True
    assert _shipper(repo, world).ship("minor") == "v2.14.0"
    assert not any(c[:3] in {("gh", "pr", "create"), ("gh", "pr", "list")} for c in world.calls)
    assert any(c[:3] == ("git", "tag", "-s") for c in world.calls)
    assert (repo / "VERSION").read_text(encoding="utf-8") == "2.14.0\n"


def test_a_rerun_after_the_tag_does_not_bump_a_second_time(repo):
    """The tag is out and main is not back in developer yet: the rerun finishes that
    release. It must not bump a second version, which was the old stop's whole point."""
    world = FakeWorld(repo)
    world.already_merged = True
    world.developer_synced = False
    assert _shipper(repo, world).ship("patch") == "v2.13.0"
    assert (repo / "VERSION").read_text(encoding="utf-8") == "2.13.0\n"
    assert not any(c[:2] == ("git", "commit") for c in world.calls)


def test_a_rerun_after_the_tag_resumes_at_the_release_and_the_install(repo):
    """The run used to stop with "still finishing": a rerun now finishes the release it
    finds (release workflow, sync, install), with no pull request and no new tag."""
    world = FakeWorld(repo)
    world.already_merged = True
    world.developer_synced = False
    world.remote_tags = ["v2.13.0"]
    installed = installed_engine(repo, "2.13.0")
    world.install_root = installed
    shipper = _shipper(repo, world)
    shipper.engine_root = installed / "03-INFRA"
    assert shipper.ship("patch", install=True) == "v2.13.0"
    assert not any(c[:3] == ("gh", "pr", "create") for c in world.calls)
    assert not any(c[:3] == ("git", "tag", "-s") for c in world.calls), "the published tag is reused"
    assert world.launches("nexgen", "update")


def test_a_tag_left_local_by_an_interrupted_run_is_verified_and_pushed(repo):
    world = FakeWorld(repo)
    world.already_merged = True
    world.developer_synced = False
    shipper = _shipper(repo, world)
    assert shipper.ship("patch") == "v2.13.0"
    assert not any(c[:3] == ("git", "tag", "-s") for c in world.calls), "the local tag is reused, not remade"
    assert any(c[:2] == ("git", "push") and c[-1] == "v2.13.0" for c in world.calls)
    assert ("git", "tag", "-d", "v2.13.0") not in world.calls


def test_a_local_tag_pointing_somewhere_else_is_refused(repo):
    world = FakeWorld(repo)
    world.already_merged = True
    world.developer_synced = False
    world.local_tag_commits = {"v2.13.0": "c" * 40}
    with pytest.raises(ShipStop, match="points at"):
        _shipper(repo, world).ship("patch")
    assert not any(c[:2] == ("git", "push") and c[-1] == "v2.13.0" for c in world.calls)


def test_a_machine_on_nightly_updates_along_its_channel_not_to_the_tag(repo, installed_launcher):
    """v2.16.5: the shipping machine follows nightly, whose updater refuses a named tag;
    the install named one and was rolled back. On a moving channel it updates along the
    channel and accepts the release or anything newer."""
    world = FakeWorld(repo)
    world.install_root = installed_engine(repo, "2.13.0")
    original = world.answer

    def answer(argv):
        if argv[:2] == ("nexgen", "--version"):
            return "NeXgen Engine 2.13.0 (nightly abc1234)", 0
        if argv[:2] == ("nexgen", "update"):
            if "--target" in argv:
                return "nexgen-update: ERROR: --target names a released tag; it cannot be combined with a moving channel", 1
            (world.install_root / "VERSION").write_text("2.14.1\n", encoding="utf-8")
            return "installed developer@abc1234", 0
        return original(argv)

    world.answer = answer
    _installer(repo, world).install("2.14.0")

    updates = world.launches("nexgen", "update")
    assert updates and all("--target" not in call for call in updates)
