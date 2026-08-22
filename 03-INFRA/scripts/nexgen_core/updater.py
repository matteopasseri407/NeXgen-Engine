#!/usr/bin/env python3
"""Deliberate, cross-platform NeXgen Engine release updater (v2).

The command updates only to a released semantic-version tag. It never stashes
or commits user-authored data, never checks out a detached HEAD, and never
rolls back automatically. In a split install it may commit the mechanical
engine pin, and only that exact file, through ``vault-push``. A dirty engine or
data repository stops the update before the engine ref moves.

``--unattended`` is the one behaviour change from that interactive contract:
it skips the confirmation prompt the way ``--yes`` does, but caps how large a
jump it will take without a human reading the release notes first. The
ceiling defaults to the smallest possible jump -- a patch release, same major
and minor -- because a machine that changes its own behaviour overnight
changed it without anyone choosing that. A minor or major release is refused
with the interactive command to run by hand; the interactive path itself is
unchanged and still has no ceiling. Truth in advertising: the bad-signature
check below is real rejection (a provably invalid signature stops the update),
but an *unverifiable* signature only prints a warning and continues -- that is
not enforcement, and nothing here or in `--help` claims otherwise.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from nexgen_core.paths import resolve_home

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

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

    data_hint = environ.get("AGENT_VAULT_DATA") or environ.get("KNOWLEDGE_VAULT_PATH")
    if data_hint:
        data_repo = _git_root(Path(data_hint), label="vault data path")
    else:
        home_hint = environ.get("HOME") or environ.get("USERPROFILE")
        default_home = Path(home_hint).expanduser() if home_hint else resolve_home()
        default_data = default_home / "KnowledgeVault"
        if default_data.resolve() == engine_repo or (default_data / ".git").exists():
            data_repo = _git_root(default_data, label="default KnowledgeVault")
        else:
            data_repo = engine_repo
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


def _is_ancestor(engine_repo: Path, target: str) -> bool:
    relation = _git(
        engine_repo,
        "merge-base",
        "--is-ancestor",
        "HEAD",
        target,
        check=False,
    )
    return relation.returncode == 0


def _assert_fast_forward(engine_repo: Path, target: str) -> None:
    if not _is_ancestor(engine_repo, target):
        raise UpdateError(
            f"{target} is not a fast-forward from the current engine branch; "
            "resolve local branch history manually"
        )


def _assert_merge_identity(engine_repo: Path) -> None:
    identity = _git(engine_repo, "var", "GIT_COMMITTER_IDENT", check=False)
    if identity.returncode != 0:
        raise UpdateError(
            "the single-clone update needs a Git user.name and user.email to "
            "preserve local data commits in a merge"
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


def _commit_split_pin(
    *,
    pin_file: Path,
    target_head: str,
    target: str,
    data_repo: Path,
    vault_push: str,
) -> None:
    try:
        pin_file.write_text(f"{target_head}\n", encoding="utf-8")
    except OSError as exc:
        raise UpdateError(f"cannot update engine pin {pin_file}: {exc}") from exc
    relative_pin = pin_file.relative_to(data_repo).as_posix()
    result = _run(
        [
            vault_push,
            "-m",
            f"Pin NeXgen Engine to {target}",
            "--",
            relative_pin,
        ],
        cwd=data_repo,
        check=False,
        capture=False,
    )
    if result.returncode != 0:
        raise UpdateError("vault-push could not commit and publish the updated engine pin")
    try:
        published_pin = pin_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdateError(f"cannot verify engine pin {pin_file}: {exc}") from exc
    if published_pin != target_head:
        raise UpdateError("engine pin does not match the installed release after vault-push")
    _assert_clean(data_repo, label="data")


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


def _assert_within_unattended_ceiling(current: str, target: str) -> None:
    """Refuses an unattended jump larger than a patch release.

    The ceiling defaults to the smallest possible jump: same major, same
    minor. A minor or major release changes behaviour a human has not read
    about yet, so unattended mode declines it. The message names the
    recovery -- running the interactive command by hand -- not the check
    that failed, per the self-upgrader's contract.
    """
    current_major, current_minor, _ = _version_tuple(current)
    target_major, target_minor, _ = _version_tuple(target)
    if (target_major, target_minor) != (current_major, current_minor):
        raise UpdateError(
            f"unattended update refuses the jump from v{current} to {target} "
            "(minor or major version change); recover by running "
            f"'nexgen-update --target {target.removeprefix('v')}' interactively "
            "to review the release notes and confirm it by hand"
        )


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
    parser.add_argument(
        "--unattended",
        action="store_true",
        help=(
            "apply a released update without a confirmation prompt, but only a "
            "patch-level jump (same major.minor); refuses a minor or major "
            "release instead of asking"
        ),
    )
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
    pin_file: Path | None = None
    try:
        engine_repo, data_repo = resolve_repositories(env)
        split_topology = data_repo != engine_repo
        current = _current_version(engine_repo)
        target = _target_tag(engine_repo, args.target)
        target_version = _assert_target_version(engine_repo, target)
        target_head = _git(engine_repo, "rev-parse", f"{target}^{{commit}}").stdout.strip()
        print(f"Engine: {engine_repo}")
        print(f"Data:   {data_repo}")
        print(f"Current: v{current}")
        print(f"Latest released target: {target}")

        if _version_tuple(target_version) < _version_tuple(current):
            # Two different situations, and only one of them is a fault.
            #
            # Somebody who names an older version explicitly asked for a
            # thing that will not happen, and has to be told so.
            if args.target:
                raise UpdateError(
                    f"refusing to downgrade from v{current} to {target}; "
                    f"use the manual recovery runbook"
                )
            # Being ahead of the newest release is simply what running a
            # pre-release looks like. Reporting it hourly as a refusal told a
            # person that something had gone wrong when nothing had.
            print(
                f"v{current} is ahead of the newest release ({target}): nothing to update to. "
                f"Expected on a pre-release; the next published release will be picked up."
            )
            return 0
        if _version_tuple(target_version) == _version_tuple(current):
            print("NeXgen Engine is already at the requested released version.")
            return 0

        if args.unattended:
            _assert_within_unattended_ceiling(current, target)

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
        if split_topology:
            _assert_clean(data_repo, label="data")
            _assert_fast_forward(engine_repo, target)
        elif not _is_ancestor(engine_repo, target):
            _assert_merge_identity(engine_repo)

        sync = which("agent-sync")
        doctor = which("agent-doctor")
        vault_push = which("vault-push")
        pin_candidate = data_repo / "99-INDEX" / "ENGINE-PIN.txt"
        pin_file = pin_candidate if split_topology and pin_candidate.is_file() else None
        if split_topology and not sync:
            raise UpdateError(
                "split engine/data topology requires agent-sync; use the one-time "
                "manual bootstrap in docs/upgrade.md"
            )
        if pin_file and not vault_push:
            raise UpdateError(
                "the split engine pin requires vault-push; run the one-time "
                "manual bootstrap in docs/upgrade.md"
            )
        print("\nPlan:")
        merge_mode = "--ff-only" if split_topology else "--no-edit"
        print(f"  1. git merge {merge_mode} {target}")
        step = 2
        if pin_file:
            print(f"  {step}. update 99-INDEX/ENGINE-PIN.txt through vault-push")
            step += 1
        if sync:
            print(f"  {step}. agent-sync apply")
        else:
            print(f"  {step}. MINIMAL install, no provisioner detected")
        step += 1
        if doctor:
            print(f"  {step}. agent-doctor --strict --summary")
        else:
            print(f"  {step}. visual MINIMAL verification")

        if not (args.yes or args.unattended) and not _confirm(input_fn, current=current, target=target):
            print("Update cancelled. No installed files or branch were changed.")
            return 0

        pre_fail, pre_doctor_rc = _doctor(which, data_repo=data_repo)
        if doctor and pre_fail is None:
            raise UpdateError("pre-upgrade doctor did not return a readable FAIL summary")
        if doctor and pre_doctor_rc != 0 and pre_fail == 0:
            raise UpdateError("pre-upgrade doctor returned an inconsistent result")
        previous_head = _git(engine_repo, "rev-parse", "HEAD").stdout.strip()
        merge = _git(engine_repo, "merge", merge_mode, target, check=False)
        if merge.returncode != 0:
            _git(engine_repo, "merge", "--abort", check=False)
            detail = (merge.stderr or merge.stdout or "merge failed").strip()
            raise UpdateError(
                f"merge {merge_mode} {target}: {detail}\n"
                f"The merge was rolled back; {engine_repo} is still at {previous_head[:9]}."
            )
        if merge.stdout:
            print(merge.stdout.rstrip())

        if pin_file and vault_push:
            try:
                _commit_split_pin(
                    pin_file=pin_file,
                    target_head=target_head,
                    target=target,
                    data_repo=data_repo,
                    vault_push=vault_push,
                )
            except UpdateError as exc:
                raise PostMergeError(
                    f"engine tag merged, but the private engine pin was not published: {exc}",
                    previous_head=previous_head,
                    engine_repo=engine_repo,
                ) from exc

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
        if pin_file:
            print(
                "If 99-INDEX/ENGINE-PIN.txt moved, realign it to the same commit "
                "through vault-push before running agent-sync again.",
                file=sys.stderr,
            )
        return 1
    except UpdateError as exc:
        print(f"nexgen-update: ERROR: {exc}", file=sys.stderr)
        return 1


class EngineUpdater:
    """Support class for invoking the updater programmatically."""

    @staticmethod
    def main(argv: Sequence[str] | None = None, **kwargs: Any) -> int:
        return main(argv, **kwargs)

    @staticmethod
    def check_updates() -> tuple[bool, str, str]:
        """Checks whether new released versions exist, without applying them."""
        try:
            engine_repo, _ = resolve_repositories(os.environ)
            curr = _current_version(engine_repo)
            tags = _released_tags(engine_repo)
            if tags:
                latest = tags[0].removeprefix("v")
                has_up = _version_tuple(latest) > _version_tuple(curr)
                return has_up, f"v{curr}", f"v{latest}"
            return False, f"v{curr}", f"v{curr}"
        except Exception:
            return False, "unknown", "unknown"


if __name__ == "__main__":
    raise SystemExit(main())

