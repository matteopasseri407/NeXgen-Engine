#!/usr/bin/env python3
"""Deliberate, cross-platform NeXgen Engine release updater.

The command updates only to a released semantic-version tag. It never stashes
or commits user data, never checks out a detached HEAD, and never rolls back
automatically. A dirty engine or data repository stops the update before the
engine ref moves.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Sequence


SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
CHANGELOG_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\].*$", re.MULTILINE)
BAD_SIGNATURE_STATES = {"B", "R", "X", "Y"}
UNVERIFIED_SIGNATURE_STATES = {"E", "N"}


class UpdateError(RuntimeError):
    """A safe, actionable update failure."""


class PostMergeError(UpdateError):
    """The engine ref moved, but provisioning or verification failed."""

    def __init__(self, message: str, *, previous_head: str, engine_repo: Path):
        super().__init__(message)
        self.previous_head = previous_head
        self.engine_repo = engine_repo


def _run(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=str(cwd),
        check=False,
        text=True,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise UpdateError(f"{' '.join(args)}: {detail}")
    return result


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", str(repo), *args], cwd=repo, check=check)


def _git_root(candidate: Path, *, label: str) -> Path:
    candidate = candidate.expanduser().resolve()
    if not candidate.exists():
        raise UpdateError(f"{label} path does not exist: {candidate}")
    lookup = candidate if candidate.is_dir() else candidate.parent
    probe = _run(
        ["git", "-C", str(lookup), "rev-parse", "--show-toplevel"],
        cwd=lookup,
        check=False,
    )
    if probe.returncode != 0:
        raise UpdateError(f"{label} is not inside a Git repository: {candidate}")
    return Path(probe.stdout.strip()).resolve()


def resolve_repositories(environ: Mapping[str, str]) -> tuple[Path, Path]:
    engine_hint = environ.get("AGENT_ENGINE_ROOT")
    if engine_hint:
        engine_repo = _git_root(Path(engine_hint), label="AGENT_ENGINE_ROOT")
    else:
        engine_repo = _git_root(Path(__file__).resolve(), label="engine script")

    if not (engine_repo / "VERSION").is_file() or not (engine_repo / "03-INFRA").is_dir():
        raise UpdateError(f"not a complete NeXgen Engine clone: {engine_repo}")

    data_hint = environ.get("AGENT_VAULT_DATA")
    data_repo = _git_root(Path(data_hint), label="AGENT_VAULT_DATA") if data_hint else engine_repo
    return engine_repo, data_repo


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value.strip())
    if not match:
        raise UpdateError(f"invalid semantic version: {value!r}")
    return tuple(int(part) for part in match.groups())


def _current_version(engine_repo: Path) -> str:
    value = (engine_repo / "VERSION").read_text(encoding="utf-8").strip()
    _version_tuple(value)
    return value


def _released_tags(engine_repo: Path) -> list[str]:
    _git(engine_repo, "fetch", "--tags", "origin")
    remote_main = _git(engine_repo, "rev-parse", "--verify", "origin/main", check=False)
    if remote_main.returncode != 0:
        raise UpdateError("origin/main is missing; cannot prove which tags are public releases")
    remote_tags = _git(engine_repo, "ls-remote", "--tags", "--refs", "origin").stdout
    published = {
        ref.removeprefix("refs/tags/")
        for line in remote_tags.splitlines()
        if len((fields := line.split())) == 2
        for ref in [fields[1]]
        if ref.startswith("refs/tags/")
    }
    result = _git(engine_repo, "tag", "--merged", "origin/main", "--sort=-version:refname")
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if SEMVER.fullmatch(line.strip()) and line.strip() in published
    ]


def _target_tag(engine_repo: Path, requested: str | None) -> str:
    tags = _released_tags(engine_repo)
    if not tags:
        raise UpdateError("origin/main has no released semantic-version tags")
    if not requested:
        return tags[0]
    requested = requested if requested.startswith("v") else f"v{requested}"
    if requested not in tags:
        raise UpdateError(f"{requested} is not a released tag merged into origin/main")
    return requested


def _changelog_between(engine_repo: Path, current: str, target: str) -> str:
    changelog = _git(engine_repo, "show", f"{target}:CHANGELOG.md").stdout
    current_v = _version_tuple(current)
    target_v = _version_tuple(target)
    matches = list(CHANGELOG_HEADING.finditer(changelog))
    sections: list[str] = []
    for index, match in enumerate(matches):
        version = _version_tuple(match.group(1))
        if current_v < version <= target_v:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(changelog)
            sections.append(changelog[match.start():end].strip())
    if not sections:
        raise UpdateError(f"CHANGELOG.md has no entry between v{current} and {target}")
    return "\n\n".join(sections)


def _assert_clean(repo: Path, *, label: str) -> None:
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=normal").stdout.strip()
    if status:
        sample = "\n".join(status.splitlines()[:8])
        raise UpdateError(
            f"{label} repository is dirty; commit or stash your work yourself before updating:\n{sample}"
        )


def _assert_attached_branch(engine_repo: Path) -> None:
    branch = _git(engine_repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch.returncode != 0 or not branch.stdout.strip():
        raise UpdateError("engine checkout is detached; attach it to its normal branch before updating")


def _assert_fast_forward(engine_repo: Path, target: str) -> None:
    relation = _git(
        engine_repo,
        "merge-base",
        "--is-ancestor",
        "HEAD",
        target,
        check=False,
    )
    if relation.returncode != 0:
        raise UpdateError(
            f"{target} is not a fast-forward from the current engine branch; "
            "resolve local branch history manually"
        )


def _assert_target_version(engine_repo: Path, target: str) -> str:
    expected = target.removeprefix("v")
    release_version = _git(engine_repo, "show", f"{target}:VERSION").stdout.strip()
    _version_tuple(release_version)
    if release_version != expected:
        raise UpdateError(
            f"release {target} contains VERSION={release_version!r}; expected {expected!r}"
        )
    return release_version


def _signature_state(engine_repo: Path, target: str) -> str:
    state = _git(engine_repo, "log", "-1", "--format=%G?", target).stdout.strip() or "N"
    if state in BAD_SIGNATURE_STATES:
        raise UpdateError(f"release commit for {target} has an invalid or expired signature (state {state})")
    return state


def _doctor(which: Callable[[str], str | None], *, data_repo: Path) -> tuple[int | None, int]:
    doctor = which("agent-doctor")
    if not doctor:
        return None, 0
    result = _run([doctor, "--strict", "--summary"], cwd=data_repo, check=False)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    match = re.search(r"\bFAIL=(\d+)\b", f"{result.stdout}\n{result.stderr}")
    return (int(match.group(1)) if match else None), result.returncode


def _confirm(input_fn: Callable[[str], str], *, current: str, target: str) -> bool:
    try:
        answer = input_fn(f"Upgrade NeXgen Engine from v{current} to {target}? [y/N] ")
    except EOFError:
        raise UpdateError("confirmation required; review the plan, then rerun with --yes") from None
    return answer.strip().lower() in {"y", "yes"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexgen-update",
        description="Check and deliberately install a released NeXgen Engine tag.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check and show release notes without moving the installed branch",
    )
    parser.add_argument("--yes", action="store_true", help="confirm the displayed update plan non-interactively")
    parser.add_argument("--target", metavar="vX.Y.Z", help="install this released tag instead of the newest one")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    input_fn: Callable[[str], str] = input,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    previous_head = ""
    engine_repo: Path | None = None
    try:
        engine_repo, data_repo = resolve_repositories(env)
        current = _current_version(engine_repo)
        target = _target_tag(engine_repo, args.target)
        target_version = _assert_target_version(engine_repo, target)
        print(f"Engine: {engine_repo}")
        print(f"Data:   {data_repo}")
        print(f"Current: v{current}")
        print(f"Latest released target: {target}")

        if _version_tuple(target_version) < _version_tuple(current):
            raise UpdateError(
                f"refusing to downgrade from v{current} to {target}; use the manual recovery runbook"
            )
        if _version_tuple(target_version) == _version_tuple(current):
            print("NeXgen Engine is already at the requested released version.")
            return 0

        print("\nRelease notes:\n")
        print(_changelog_between(engine_repo, current, target))
        signature = _signature_state(engine_repo, target)
        if signature in UNVERIFIED_SIGNATURE_STATES:
            print(
                f"\nWARNING: this Git installation could not verify the release "
                f"commit signature for {target} (state {signature}).",
                file=sys.stderr,
            )
        else:
            print(f"\nRelease commit signature state: {signature}.")

        if args.check:
            print("\nCheck only. No installed files or branch were changed.")
            return 0

        _assert_attached_branch(engine_repo)
        _assert_clean(engine_repo, label="engine")
        if data_repo != engine_repo:
            _assert_clean(data_repo, label="data")
        _assert_fast_forward(engine_repo, target)

        sync = which("agent-sync")
        doctor = which("agent-doctor")
        if data_repo != engine_repo and not sync:
            raise UpdateError(
                "split engine/data topology requires agent-sync; use the one-time "
                "manual bootstrap in docs/upgrade.md"
            )
        print("\nPlan:")
        print(f"  1. git merge --ff-only {target}")
        if sync:
            print("  2. agent-sync apply")
        else:
            print("  2. MINIMAL install, no provisioner detected")
        if doctor:
            print("  3. agent-doctor --strict --summary")
        else:
            print("  3. visual MINIMAL verification")

        if not args.yes and not _confirm(input_fn, current=current, target=target):
            print("Update cancelled. No installed files or branch were changed.")
            return 0

        pre_fail, pre_doctor_rc = _doctor(which, data_repo=data_repo)
        if doctor and pre_fail is None:
            raise UpdateError("pre-upgrade doctor did not return a readable FAIL summary")
        if doctor and pre_doctor_rc != 0 and pre_fail == 0:
            raise UpdateError("pre-upgrade doctor returned an inconsistent result")
        previous_head = _git(engine_repo, "rev-parse", "HEAD").stdout.strip()
        merge = _git(engine_repo, "merge", "--ff-only", target)
        if merge.stdout:
            print(merge.stdout.rstrip())

        if sync:
            provision = _run([sync, "apply"], cwd=data_repo, check=False, capture=False)
            if provision.returncode != 0:
                raise PostMergeError(
                    "engine tag merged, but agent-sync apply failed",
                    previous_head=previous_head,
                    engine_repo=engine_repo,
                )
        else:
            print("MINIMAL install: reopen the configured CLI and verify instructions, tools, and skills visually.")

        post_fail, post_doctor_rc = _doctor(which, data_repo=data_repo)
        if doctor and post_fail is None:
            raise PostMergeError(
                "engine tag merged, but the final doctor did not return a readable summary",
                previous_head=previous_head,
                engine_repo=engine_repo,
            )
        if doctor and post_doctor_rc != 0 and post_fail == 0:
            raise PostMergeError(
                "engine tag merged, but the final doctor returned an inconsistent result",
                previous_head=previous_head,
                engine_repo=engine_repo,
            )
        if pre_fail is not None and post_fail is not None and post_fail > pre_fail:
            raise PostMergeError(
                f"final doctor introduced new failures: before={pre_fail}, after={post_fail}",
                previous_head=previous_head,
                engine_repo=engine_repo,
            )

        installed = _current_version(engine_repo)
        if installed != target_version:
            raise PostMergeError(
                f"VERSION is {installed}, expected {target_version}",
                previous_head=previous_head,
                engine_repo=engine_repo,
            )
        if post_fail is None:
            print(f"\nNeXgen Engine {target} installed in MINIMAL mode.")
            print("Automatic doctor verification was unavailable on this machine.")
        elif post_fail == 0:
            print(f"\nNeXgen Engine {target} installed and verified on this machine.")
        else:
            print(f"\nNeXgen Engine {target} installed with no new doctor failures.")
            print(f"The machine still has {post_fail} pre-existing doctor failure(s).")
        print("Other machines remain behind until they run nexgen-update too.")
        print(
            "Cloud-Server note: the VPS is a separate install; follow "
            "03-INFRA/deploy/README.md before redeploying it."
        )
        return 0
    except PostMergeError as exc:
        print(f"nexgen-update: ERROR: {exc}", file=sys.stderr)
        print(
            "The updater will not roll back automatically. After reviewing the failure, the recoverable rollback is:\n"
            f"  git -C {exc.engine_repo} reset --hard {exc.previous_head}",
            file=sys.stderr,
        )
        return 1
    except UpdateError as exc:
        print(f"nexgen-update: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
