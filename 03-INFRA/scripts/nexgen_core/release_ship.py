#!/usr/bin/env python3
"""One command from `developer` to a published, signed release.

The steps were always the same and always done by hand: bump, open the
`developer -> main` pull request, wait for its checks, merge, wait for the CI
on the merge commit (the release workflow refuses a tag before that is green),
sign the tag, check that installed copies would accept it, push it, wait for
the release workflow, and wait for `sync-developer` to bring main back. Each
wait is a place where a person either stares at a terminal or forgets a step.

`ship` does them in order and stops at the first one that is not green, with
the step and the reason on one line. It signs with whatever key this
machine's git is configured to sign with (`user.signingkey`, `gpg.format`):
every maintainer machine has its own key, listed in `trust/release-signers.txt`,
and the tag is verified against the *installed* copy's list before it is
pushed, so a key that the fleet would refuse never reaches the remote.

It is safe to run again after a stop: a version that is already bumped is not
bumped twice, and an open release pull request is reused.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.files import atomic_write_text  # noqa: E402
from nexgen_core.paths import resolve_engine_root  # noqa: E402
from nexgen_core.release import bump_version_files, compute_next_version, is_semver, newer_version  # noqa: E402

#: Checks that fail on purpose on this pull request and say nothing about the
#: release: the lane guard rejects pushes that do not come from a feature lane.
IGNORED_CHECKS = ("lane-guard",)

_PENDING = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED"}
_GREEN = {"SUCCESS", "SKIPPED", "NEUTRAL"}


class ShipStop(Exception):
    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason


def _default_run(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # Resolved through PATH(EXT) so `gh.exe` and `nexgen.cmd` work on Windows too.
    exe = shutil.which(argv[0]) or argv[0]
    return subprocess.run(
        [exe, *argv[1:]], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=1800,
    )


@dataclass
class Shipper:
    repo: Path
    engine_root: Path
    run: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]] = _default_run
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] = print
    poll_seconds: float = 30.0
    wait_limit_seconds: float = 3600.0
    branch: str = "developer"
    install_log: Path | None = None
    _step: str = field(default="start", init=False)

    # -- plumbing ---------------------------------------------------------

    def _do(self, *argv: str, what: str = "") -> str:
        result = self.run(argv, self.repo)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise ShipStop(self._step, f"{what or ' '.join(argv[:3])} failed: {detail[-1] if detail else result.returncode}")
        return result.stdout.strip()

    def _enter(self, step: str) -> None:
        self._step = step
        self.log(f"== {step}")

    def _wait(self, probe: Callable[[], str | None], what: str) -> str:
        """Polls `probe` until it returns a value. `probe` raises to stop."""
        waited = 0.0
        while True:
            value = probe()
            if value is not None:
                return value
            if waited >= self.wait_limit_seconds:
                raise ShipStop(self._step, f"gave up waiting for {what} after {int(waited)}s")
            self.sleep(self.poll_seconds)
            waited += self.poll_seconds

    # -- steps ------------------------------------------------------------

    def _prepare(self, bump: str, notes: str) -> str:
        self._enter("prepare")
        current_branch = self._do("git", "rev-parse", "--abbrev-ref", "HEAD")
        if current_branch != self.branch:
            raise ShipStop(self._step, f"run it from '{self.branch}', not '{current_branch}'")
        if self._do("git", "status", "--porcelain"):
            raise ShipStop(self._step, "the working tree has uncommitted changes")
        self._do("git", "fetch", "--tags", "origin")
        self._do("git", "merge", "--ff-only", f"origin/{self.branch}", what="fast-forward to origin")

        version = (self.repo / "VERSION").read_text(encoding="utf-8").strip()
        tags = [t for t in self._do("git", "tag", "--list", "v*", "--sort=-v:refname").split() if is_semver(t[1:])]
        newest = tags[0][1:] if tags else "0.0.0"
        if newer_version(version, newest):
            self.log(f"   VERSION {version} is already ahead of v{newest}: not bumping again")
            return version

        # VERSION equal to the newest tag is the normal start of a release, and
        # also what a rerun after a pushed tag looks like. Only the second one
        # has main not yet merged back into developer: bumping there would
        # release a version nobody asked for.
        self._do("git", "fetch", "origin", "main")
        if self.run(["git", "merge-base", "--is-ancestor", "origin/main", f"origin/{self.branch}"], self.repo).returncode != 0:
            raise ShipStop(self._step, f"v{version} is still finishing (main not yet back in {self.branch}); "
                           "wait for sync-developer, then 'nexgen update' to install it")
        target = compute_next_version(version, bump)
        try:
            modified = bump_version_files(self.repo, target, notes)
        except ValueError as exc:
            raise ShipStop(self._step, str(exc)) from None
        preflight = self.run([sys.executable, str(self.repo / "03-INFRA" / "scripts" / "nexgen_core" / "release.py"), "preflight"], self.repo)
        if preflight.returncode != 0:
            self._do("git", "checkout", "--", *(str(p.relative_to(self.repo)) for p in modified))
            last = (preflight.stderr or preflight.stdout).strip().splitlines()
            raise ShipStop(self._step, f"preflight: {last[-1] if last else 'failed'} (bump undone)")
        self._do("git", "add", *(str(p.relative_to(self.repo)) for p in modified))
        self._do("git", "commit", "-q", "-m", f"Release v{target}")
        self._do("git", "push", "-q", "origin", self.branch)
        self.log(f"   bumped {version} -> {target}")
        return target

    def _already_merged(self) -> str | None:
        """On a rerun after the merge, main already has the release commit: no second pull request."""
        self._do("git", "fetch", "origin", "main")
        head = self._do("git", "rev-parse", "HEAD")
        if self.run(["git", "merge-base", "--is-ancestor", head, "origin/main"], self.repo).returncode != 0:
            return None
        self.log("   already merged into main: resuming after the merge")
        return self._do("git", "rev-parse", "origin/main")

    def _open_pull_request(self, version: str) -> int:
        self._enter("pull request")
        existing = json.loads(self._do(
            "gh", "pr", "list", "--base", "main", "--head", self.branch,
            "--state", "open", "--json", "number",
        ) or "[]")
        if existing:
            number = int(existing[0]["number"])
            self.log(f"   reusing #{number}")
            return number
        url = self._do(
            "gh", "pr", "create", "--base", "main", "--head", self.branch,
            "--title", f"Release v{version}",
            "--body", f"Release v{version}. Notes in CHANGELOG.md.",
        )
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        self.log(f"   opened #{number}")
        return number

    def _checks_verdict(self, number: int, head: str) -> str | None:
        data = json.loads(self._do("gh", "pr", "view", str(number), "--json", "headRefOid,statusCheckRollup"))
        if data.get("headRefOid") != head:
            return None  # GitHub has not seen the pushed head yet
        checks = data.get("statusCheckRollup") or []
        if not checks:
            return None
        failed = []
        for check in checks:
            name = check.get("name") or check.get("context") or "?"
            if any(ignored in name for ignored in IGNORED_CHECKS):
                continue
            status = (check.get("status") or "").upper()
            state = (check.get("conclusion") or check.get("state") or "").upper()
            if status in _PENDING or (status and status != "COMPLETED") or state in _PENDING or not state:
                return None
            if state not in _GREEN:
                failed.append(name)
        if failed:
            raise ShipStop(self._step, "failing checks: " + ", ".join(sorted(set(failed))))
        return "green"

    def _merge(self, number: int) -> str:
        self._enter(f"checks on #{number}")
        head = self._do("git", "rev-parse", "HEAD")
        self._wait(lambda: self._checks_verdict(number, head), "the pull request checks")
        self._enter("merge")
        self._do("gh", "pr", "merge", str(number), "--merge", what="merge")
        self._do("git", "fetch", "origin", "main")
        merged = self._do("git", "rev-parse", "origin/main")
        if self.run(["git", "merge-base", "--is-ancestor", head, merged], self.repo).returncode != 0:
            raise ShipStop(self._step, f"origin/main {merged[:8]} does not contain the release commit {head[:8]}")
        return merged

    def _run_verdict(self, query: Sequence[str], branch: str = "") -> str | None:
        runs = json.loads(self._do(*query) or "[]")
        if branch:
            runs = [r for r in runs if r.get("headBranch") == branch]
        if not runs:
            return None
        run = runs[0]
        if run.get("status") != "completed":
            return None
        if run.get("conclusion") != "success":
            return f"failed:{run.get('databaseId')}"
        return f"ok:{run.get('databaseId')}"

    def _wait_main_ci(self, sha: str) -> None:
        self._enter(f"CI on main {sha[:8]}")
        query = ("gh", "run", "list", "--commit", sha, "--workflow", "ci.yml", "--limit", "1",
                 "--json", "databaseId,status,conclusion")
        verdict = self._wait(lambda: self._run_verdict(query), "the CI on main")
        if verdict.startswith("failed"):
            raise ShipStop(self._step, f"CI on main failed (run {verdict.split(':')[1]})")

    def _tag(self, version: str, sha: str) -> str:
        tag = f"v{version}"
        self._enter(f"tag {tag}")
        self._do("git", "tag", "-s", tag, "-m", f"Release {tag}", sha, what="signing the tag")
        trust = self.engine_root / "scripts" / "nexgen_core" / "release_trust.py"
        verdict = self.run(
            [sys.executable, str(trust), tag, "--repo", str(self.repo),
             "--trust-dir", str(self.engine_root / "agent-universal-layer" / "trust")],
            self.repo,
        )
        if verdict.returncode != 0:
            self.run(["git", "tag", "-d", tag], self.repo)
            line = (verdict.stdout or verdict.stderr).strip().splitlines()
            raise ShipStop(self._step, f"installed copies would refuse it ({line[-1] if line else 'no verdict'}); tag deleted, nothing pushed")
        self._do("git", "push", "-q", "origin", tag)
        return tag

    def _wait_release(self, tag: str) -> None:
        self._enter("release workflow")
        query = ("gh", "run", "list", "--workflow", "release.yml", "--limit", "5",
                 "--json", "databaseId,status,conclusion,headBranch")
        verdict = self._wait(lambda: self._run_verdict(query, tag), "the release workflow")
        if verdict.startswith("failed"):
            # The one known transient: the workflow can look for the green CI
            # on main a moment before GitHub reports it. One rerun, no more.
            run_id = verdict.split(":")[1]
            self.log(f"   run {run_id} failed, rerunning once")
            self._do("gh", "run", "rerun", run_id)
            self.sleep(self.poll_seconds)
            verdict = self._wait(lambda: self._run_verdict(query, tag), "the release workflow rerun")
            if verdict.startswith("failed"):
                raise ShipStop(self._step, f"release workflow failed twice (run {run_id})")
        info = json.loads(self._do("gh", "release", "view", tag, "--json", "isDraft,assets"))
        if info.get("isDraft") or not info.get("assets"):
            raise ShipStop(self._step, f"{tag} is published as a draft or without assets")

    def _sync_developer(self, merged: str) -> None:
        self._enter("sync developer")

        def synced() -> str | None:
            self._do("git", "fetch", "origin", self.branch)
            ok = self.run(["git", "merge-base", "--is-ancestor", merged, f"origin/{self.branch}"], self.repo)
            return "ok" if ok.returncode == 0 else None

        self._wait(synced, "sync-developer to merge main back")
        self._do("git", "merge", "--ff-only", f"origin/{self.branch}", what="fast-forward to origin")

    def _installed_version(self) -> str:
        installed_file = self.engine_root.parent / "VERSION"
        return installed_file.read_text(encoding="utf-8").strip() if installed_file.is_file() else "?"

    def _install(self, version: str) -> None:
        self._enter("install")
        before = self._installed_version()
        for attempt in (1, 2):
            update = self.run(["nexgen", "update", "--yes", "--target", version], self.repo)
            if update.returncode == 0:
                break
            log = self.install_log or Path(tempfile.gettempdir()) / "nexgen-release-ship-install.log"
            atomic_write_text(log, f"{update.stdout}\n{update.stderr}")
            # A clean rollback leaves the previous version in place. The usual
            # cause is a scheduled guard or sync cycle that started in the same
            # minute, so one more try after it has finished is worth it; a
            # half-done rollback is never retried.
            if attempt == 2 or self._installed_version() != before:
                raise ShipStop(self._step, f"nexgen update failed and was rolled back; full output in {log}")
            self.log(f"   update failed and was rolled back, retrying once (output in {log})")
            self.sleep(self.poll_seconds * 4)
        # An update can end without moving anything; only the installed VERSION says it did.
        # Doctor gates on failures only: warnings (optional deps, orphans) are non-blocking by design.
        installed = self._installed_version()
        if installed != version:
            raise ShipStop(self._step, f"this machine is still on {installed}, not {version}")
        doctor = self.run(["nexgen", "doctor", "--summary"], self.repo)
        if doctor.returncode != 0:
            last = (doctor.stdout or doctor.stderr).strip().splitlines()
            raise ShipStop(self._step, f"installed, but doctor is not clean: {last[-1] if last else doctor.returncode}")

    def ship(self, bump: str, notes: str = "", install: bool = False) -> str:
        version = self._prepare(bump, notes)
        merged = self._already_merged()
        if merged is None:
            number = self._open_pull_request(version)
            merged = self._merge(number)
        self._wait_main_ci(merged)
        tag = self._tag(version, merged)
        self._wait_release(tag)
        self._sync_developer(merged)
        if install:
            self._install(version)
        return tag


def _checkout_of_cwd() -> Path | None:
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=30,
    )
    return Path(top.stdout.strip()).resolve() if top.returncode == 0 and top.stdout.strip() else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexgen release ship", description=__doc__.splitlines()[0])
    parser.add_argument("bump", nargs="?", default="patch", help="patch, minor, major or X.Y.Z (ignored if VERSION is already bumped)")
    parser.add_argument("--notes", default="", help="one line added on top of the Unreleased notes")
    parser.add_argument("--install", action="store_true", help="also run 'nexgen update --yes' and doctor on this machine")
    parser.add_argument("--repo", type=Path, default=None, help="the development checkout (default: the git repository of the current directory)")
    parser.add_argument("--poll", type=float, default=30.0, help="seconds between polls")
    args = parser.parse_args(argv)

    engine_root = resolve_engine_root()
    repo = args.repo.resolve() if args.repo else _checkout_of_cwd()
    if repo is None or not (repo / "03-INFRA" / "scripts" / "nexgen_core" / "release.py").is_file():
        print("STOP start: run it from a NeXgen Engine development checkout (or pass --repo)", file=sys.stderr)
        return 1
    if repo == engine_root.resolve().parent:
        # The installed copy only ever moves to released tags; releasing from it
        # would bump and push from the tree the updater owns.
        print("STOP start: this is the installed engine, not a development checkout", file=sys.stderr)
        return 1
    shipper = Shipper(repo=repo, engine_root=engine_root, poll_seconds=args.poll)
    try:
        tag = shipper.ship(args.bump, args.notes, args.install)
    except ShipStop as stop:
        print(f"STOP {stop.step}: {stop.reason}", file=sys.stderr)
        return 1
    print(f"OK {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
