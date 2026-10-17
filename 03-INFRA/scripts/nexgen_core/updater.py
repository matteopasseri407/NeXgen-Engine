#!/usr/bin/env python3
# nexgen: one-job reason="move this machine between engine trees: prove the target, merge it, and undo the move if the machine fails its own checks"
"""Deliberate, cross-platform NeXgen Engine release updater (v2).

The command updates only to a released semantic-version tag. It never stashes
or commits user-authored data and never checks out a detached HEAD. If an
update fails after the engine moved, it goes back to the previous head and
re-applies the old tree, interactive or not. In a split install it may commit
the mechanical engine pin, and only that exact file, through ``vault-push``. A
dirty engine or data repository stops the update before the engine ref moves.

``--unattended`` is the one behaviour change from that interactive contract:
it skips the confirmation prompt the way ``--yes`` does, but caps how large a
jump it will take without a human reading the release notes first. The
ceiling defaults to the smallest possible jump -- a patch release, same major
and minor -- because a machine that changes its own behaviour overnight
changed it without anyone choosing that. A minor or major release is refused
with the interactive command to run by hand; the interactive path itself is
unchanged and still has no ceiling.

The release tag is verified against the trust anchor shipped in the *installed*
tree (see ``release_trust``). A signature that is wrong or made by a key the
install does not pin stops the update in every mode. A release that cannot be
verified at all (no signature, no anchor, no gpg) is shown with a warning and
left to the person at the prompt, but ``--unattended`` refuses it: nobody is
there to read the warning, and an unattended update is exactly the path a
stolen push token would use.

Two more rules apply to the merge itself. It happens under the host lock, the
one every path that changes this machine takes, so it can no longer run in the
middle of a guard cycle. And an update that fails after the engine
moved is undone on the spot, by either path: the machine goes back to the
version that worked. An *unattended* failure also remembers the release as
rejected, so the next beat does not retry it every hour, and the beat raises an
alert from the exit code.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.errors import NexgenError  # noqa: E402
from nexgen_core.files import atomic_write_text  # noqa: E402
from nexgen_core.i18n import t  # noqa: E402
from nexgen_core.lock import EXIT_BUSY_MANUAL, LockTimeoutError, host_mutation  # noqa: E402
from nexgen_core.paths import resolve_home, resolve_state_dir  # noqa: E402
from nexgen_core.processes import force_stop_process_tree  # noqa: E402
from nexgen_core.release_trust import BAD, UNTRUSTED, TagVerdict, verify_release_tag  # noqa: E402
from nexgen_core import update_channel  # noqa: E402

SEMVER = update_channel.SEMVER
CHANGELOG_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\].*$", re.MULTILINE)
GIT_TIMEOUT_SECONDS = 120.0
COMMAND_TIMEOUT_SECONDS = 600.0
#: How long the merge waits for a running guard cycle before giving up.
UPDATE_LOCK_WAIT_SECONDS = 120.0
#: Exit codes the heartbeat turns into alerts. 0 and 1 keep their old meaning.
EXIT_REFUSED = 70
EXIT_ROLLED_BACK = 71
EXIT_ROLLBACK_FAILED = 72
REJECTED_FILE = "update-rejected.json"
#: This machine's own unattended ceiling, written by --set-unattended-ceiling.
CEILING_FILE = "update-unattended-ceiling"
CEILINGS = ("patch", "minor")


class UpdateError(NexgenError, RuntimeError):
    """A safe, actionable update failure."""


class UpdateRefused(UpdateError):
    """The release signature is wrong, or made by a key this install does not pin."""

    exit_code = EXIT_REFUSED


class PostMergeError(UpdateError):
    """An update mutation began and recovery needs the previous engine ref."""

    def __init__(self, message: str, *, previous_head: str, engine_repo: Path):
        super().__init__(message)
        self.previous_head = previous_head
        self.engine_repo = engine_repo


def _tree_entry(engine_repo: Path) -> list[str]:
    """The merged tree's own front door: the interpreter plus the CLI
    dispatcher living inside the checkout being installed.

    Post-merge provisioning (apply, doctor, vault push) goes through this
    and never through a PATH shim. That is what makes deleting the
    transitional `.sh`/`.ps1` launchers safe in a single release: after the
    merge removes them, a PATH lookup could dangle on a dead symlink, while
    this path is the package itself -- if it is missing the tree is corrupt
    and the update fails closed before anything moves.
    """
    entry = engine_repo / "03-INFRA" / "scripts" / "nexgen_core" / "cli" / "__init__.py"
    if not entry.is_file():
        raise UpdateError(f"engine checkout is missing its command entry: {entry}")
    return [sys.executable, str(entry)]


def _run(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    timeout = GIT_TIMEOUT_SECONDS if args and args[0] == "git" else COMMAND_TIMEOUT_SECONDS
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        proc = subprocess.Popen(
            list(args), cwd=str(cwd), text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            start_new_session=os.name == "posix",
            env=env,
        )
    except OSError as exc:
        raise UpdateError(f"cannot launch update command ({type(exc).__name__})") from exc
    group = proc.pid if os.name == "posix" else None
    # Success leaves grandchildren alone: killing the process group after a
    # successful communicate() risks SIGKILL on a reused PGID, and would take
    # down legitimately daemonized helpers. Only a still-running child (poll
    # is None: timeout, interrupt) is stopped via force_stop_process_tree.
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
            group = None
        except subprocess.TimeoutExpired as exc:
            stopped = force_stop_process_tree(proc, process_group=group)
            if stopped:
                group = None
            try:
                proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                stopped = False
            suffix = "" if stopped else "; process cleanup not confirmed"
            raise UpdateError(f"update command timeout after {timeout:g}s{suffix}; outcome may be unknown") from exc
        result = subprocess.CompletedProcess(list(args), proc.returncode, out, err)
    except OSError as exc:
        raise UpdateError(f"update command I/O failed ({type(exc).__name__})") from exc
    except BaseException:  # interrupted updater must not leave its command running
        force_stop_process_tree(proc, process_group=group)
        raise
    finally:
        if proc.poll() is None:
            force_stop_process_tree(proc, process_group=group)
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
    """Alias of the one semver parser (`update_channel.version_tuple`), kept for its many callers here."""
    return update_channel.version_tuple(value)


def _current_version(engine_repo: Path) -> str:
    value = (engine_repo / "VERSION").read_text(encoding="utf-8").strip()
    _version_tuple(value)
    return value


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


def _commits_before(engine_repo: Path, target: str) -> str:
    """What a dev channel is about to carry: the commits, since there is no release note."""
    listed = _git(engine_repo, "log", "--oneline", "--no-merges", "-n", "30", target)
    return listed.stdout.strip() or "(no commits to show)"


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
    """Kept for callers that need only the version of an already-chosen release tag."""
    return update_channel.tag_version(engine_repo, target)


def _commit_split_pin(
    *,
    pin_file: Path,
    target_head: str,
    target: str,
    data_repo: Path,
    entry: list[str],
) -> None:
    try:
        atomic_write_text(pin_file, f"{target_head}\n")
    except OSError as exc:
        raise UpdateError(f"cannot update engine pin {pin_file}: {exc}") from exc
    relative_pin = pin_file.relative_to(data_repo).as_posix()
    result = _run(
        [
            *entry,
            "vault",
            "push",
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


def _release_verdict(engine_repo: Path, target: str) -> TagVerdict:
    """Verifies the release tag against the anchor in the installed tree.

    A wrong or foreign signature is refused here, in every mode and for
    ``--check`` too: reporting "update available" for a release that no
    installed copy would accept is worse than saying so. What to do about a
    release that cannot be verified at all is the caller's policy.
    """
    verdict = verify_release_tag(engine_repo, target)
    if verdict.status in (BAD, UNTRUSTED):
        raise UpdateRefused(
            f"release {target} is not signed by a trusted release key: {verdict.detail}. "
            "Nothing was installed. If this is a legitimate release, the maintainer has to publish "
            "it with a key the installed copies pin (see SECURITY.md, 'Release signatures')"
        )
    return verdict


def _state_dir(env: Mapping[str, str]) -> Path:
    """The machine state directory of the environment the updater was given.

    Read from `env`, not from the process: the heartbeat (and the tests) hand
    the updater a mapping built for one install, and the lock and the
    rejected-release memory must live with that install, not with whatever the
    process happens to inherit.
    """
    explicit = env.get("AGENT_STATE_DIR")
    return resolve_state_dir(override=Path(explicit) if explicit else None)


def _read_rejected(state_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((state_dir / REJECTED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_rejected(state_dir: Path, target: str, reason: str) -> None:
    try:
        atomic_write_text(
            state_dir / REJECTED_FILE,
            json.dumps({"target": target, "reason": reason[:500], "at": time.time()}, indent=2) + "\n",
        )
    except OSError:
        pass  # remembering is a courtesy; failing to would only mean one retry


def _clear_rejected(state_dir: Path) -> None:
    try:
        (state_dir / REJECTED_FILE).unlink()
    except OSError:
        pass


def _read_ceiling(state_dir: Path) -> str:
    """The ceiling this machine's owner chose; anything unreadable is "patch"."""
    try:
        value = (state_dir / CEILING_FILE).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "patch"
    return value if value in CEILINGS else "patch"


def _assert_mergeable(engine_repo: Path, data_repo: Path, target: str, *, split_topology: bool) -> None:
    """The state the merge needs. Checked once early, for a clear refusal, and
    again under the host lock, because a guard cycle may have run in between."""
    _assert_attached_branch(engine_repo)
    _assert_clean(engine_repo, label="engine")
    if split_topology:
        _assert_clean(data_repo, label="data")
        _assert_fast_forward(engine_repo, target)
    elif not _is_ancestor(engine_repo, target):
        _assert_merge_identity(engine_repo)


def _roll_back(
    exc: PostMergeError,
    *,
    entry: list[str],
    data_repo: Path,
    pin_file: Path | None,
    state_dir: Path,
    target: str,
    current: str,
    unattended: bool,
) -> int:
    """Undoes an update that failed after the engine moved, and re-applies the old tree.

    Unattended updates always do this: with nobody there, leaving the engine
    half-upgraded meant the next beat read "already at the requested version"
    and the machine ran on a tree that had failed its own checks, with no
    alert. Interactive updates do it too, because a half-new machine is no
    better for having a person standing next to it. The difference is the
    memory: only an unattended rollback marks the release as rejected, since
    a person who asked for it can decide to try again.
    """
    print(f"nexgen-update: ERROR: {exc}", file=sys.stderr)
    print(f"The {'unattended ' if unattended else ''}update to {target} failed; going back to v{current}.", file=sys.stderr)
    try:
        with host_mutation("nexgen-update-rollback", state_dir=state_dir, timeout=UPDATE_LOCK_WAIT_SECONDS):
            _git(exc.engine_repo, "merge", "--abort", check=False)
            # The lock was released between the merge and the apply, so a
            # person may have committed on the engine meanwhile. Those are
            # the only non-merge commits since previous_head that the
            # release does not carry: reset --hard would erase them.
            foreign = _git(
                exc.engine_repo, "rev-list", "--no-merges", f"{exc.previous_head}..HEAD", "--not", target,
                check=False,
            ).stdout.split()
            if foreign:
                raise UpdateError(f"{len(foreign)} commit(s) made on the engine during the update would be lost")
            _git(exc.engine_repo, "reset", "--hard", exc.previous_head)
    except (LockTimeoutError, UpdateError, OSError) as rollback_exc:
        print(
            f"nexgen-update: the rollback itself failed "
            f"({rollback_exc if isinstance(rollback_exc, UpdateError) else type(rollback_exc).__name__}). "
            f"The engine is left as it is. By hand:\n  git -C {exc.engine_repo} reset --hard {exc.previous_head}",
            file=sys.stderr,
        )
        return EXIT_ROLLBACK_FAILED

    notes: list[str] = []
    if pin_file:
        try:
            _commit_split_pin(
                pin_file=pin_file, target_head=exc.previous_head, target=f"v{current}",
                data_repo=data_repo, entry=entry,
            )
        except UpdateError as pin_exc:
            notes.append(f"the engine pin was not restored: {pin_exc}")
    try:
        realigned = _run([*entry, "apply"], cwd=data_repo, check=False, capture=False)
    except (UpdateError, OSError) as apply_exc:
        notes.append(f"agent-sync apply from the restored tree could not run ({type(apply_exc).__name__})")
    else:
        if realigned.returncode != 0:
            notes.append("agent-sync apply from the restored tree also failed")
    if unattended:
        _record_rejected(state_dir, target, str(exc))
    for note in notes:
        print(f"nexgen-update: WARNING: {note}", file=sys.stderr)
    if unattended:
        print(
            f"Rolled back to v{current}. Unattended updates will skip {target}; "
            f"review it and run 'nexgen-update --target {target.removeprefix('v')}' to try again.",
            file=sys.stderr,
        )
    else:
        print(f"Rolled back to v{current}. Fix the failure above, then run 'nexgen-update' again.", file=sys.stderr)
    return EXIT_ROLLED_BACK


def _doctor(entry: list[str], *, data_repo: Path) -> tuple[int | None, int]:
    result = _run([*entry, "doctor", "--summary"], cwd=data_repo, check=False)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    match = re.search(r"\bFAIL=(\d+)\b", f"{result.stdout}\n{result.stderr}")
    return (int(match.group(1)) if match else None), result.returncode


def _assert_within_unattended_ceiling(current: str, target: str, ceiling: str = "patch") -> None:
    """Refuses an unattended jump larger than this machine's ceiling.

    The ceiling defaults to the smallest possible jump: same major, same
    minor. A minor or major release changes behaviour a human has not read
    about yet, so unattended mode declines it. A machine whose owner also
    writes the releases can raise it to "minor" (`--set-unattended-ceiling`);
    a major release is never taken unattended. The message names the
    recovery -- running the interactive command by hand -- not the check
    that failed, per the self-upgrader's contract.
    """
    current_major, current_minor, _ = _version_tuple(current)
    target_major, target_minor, _ = _version_tuple(target)
    allowed = target_major == current_major and (ceiling == "minor" or target_minor == current_minor)
    if not allowed:
        kind = "major" if target_major != current_major else "minor"
        raise UpdateError(
            f"unattended update refuses the jump from v{current} to {target} "
            f"({kind} version change, this machine's ceiling is {ceiling}); recover by running "
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
        "--allow-unverified",
        action="store_true",
        help="install even when the release signature cannot be verified (explicit opt-in, never default)",
    )
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
    parser.add_argument(
        "--channel",
        choices=update_channel.CHANNELS,
        default=None,
        help=(
            "which NeXgen Engine this machine follows: stable (released tags, the default), "
            "dev (the signed tip of developer) or dev-verified (the newest signed commit the "
            "nightly run proved). The dev channels are closed on an installation whose private "
            "config does not open them. The choice is remembered; without the flag the "
            "remembered channel is used"
        ),
    )
    parser.add_argument(
        "--set-unattended-ceiling",
        choices=CEILINGS,
        help="remember on this machine how large a jump --unattended may take (a major one never), then exit",
    )
    return parser


def _report_post_merge_error(exc: PostMergeError, pin_file: Path | None) -> int:
    print(f"nexgen-update: ERROR: {exc}", file=sys.stderr)
    print(
        "The automatic rollback could not be completed. The recoverable rollback by hand is:\n"
        f"  git -C {exc.engine_repo} reset --hard {exc.previous_head}",
        file=sys.stderr,
    )
    print(
        t(
            "Resetting the engine is only half the recovery: generated configs and views "
            "may already be half-new. Re-run provisioning from the restored tree afterwards:\n"
            "  python3 {entry} apply\n"
            "  agent-doctor --summary",
            entry=f"{exc.engine_repo}/03-INFRA/scripts/nexgen_core/cli/__init__.py",
        ),
        file=sys.stderr,
    )
    if pin_file:
        print(
            "If 99-INDEX/ENGINE-PIN.txt moved, realign it to the same commit "
            "through vault-push before running agent-sync again.",
            file=sys.stderr,
        )
    return 1


def _recover_failed_update(
    exc: PostMergeError,
    *,
    args: argparse.Namespace,
    entry: list[str],
    data_repo: Path,
    pin_file: Path | None,
    state_dir: Path,
    target: str,
    current: str,
) -> int:
    code = _roll_back(
        exc, entry=entry, data_repo=data_repo, pin_file=pin_file,
        state_dir=state_dir, target=target, current=current, unattended=args.unattended,
    )
    if args.unattended:
        return code
    if code == EXIT_ROLLBACK_FAILED:
        # The rollback could not be finished, so the person needs the full
        # manual recovery, which is what this path printed before. The exit
        # code says so too: a wrapper must tell "restored" from "half-new".
        _report_post_merge_error(exc, pin_file)
        return EXIT_ROLLBACK_FAILED
    return 1


def _announce(engine_repo: Path, data_repo: Path, current: str, target: update_channel.TargetPlan) -> None:
    print(f"Engine: {engine_repo}")
    print(f"Data:   {data_repo}")
    print(f"Current: v{current}")
    print(f"Channel: {target.channel}")
    if target.channel_target is not None:
        print(f"Target:  {target.label} (VERSION {target.version}; {target.channel_target.detail})")
    else:
        print(f"Latest released target: {target.ref}")


def _settled(engine_repo: Path, current: str, target: update_channel.TargetPlan, *, requested: str | None) -> bool:
    """True when there is nothing to do, with the reason printed. Never moves the tree."""
    if not target.release:
        if target.rev == _git(engine_repo, "rev-parse", "HEAD").stdout.strip():
            print(f"NeXgen Engine is already at {target.label}.")
            return True
        return False
    if _version_tuple(target.version) < _version_tuple(current):
        # Two different situations, and only one of them is a fault. Somebody who
        # names an older version explicitly asked for a thing that will not
        # happen, and has to be told so. Being ahead of the newest release is
        # simply what running a pre-release looks like: reporting it hourly as a
        # refusal told a person that something had gone wrong when nothing had.
        if requested:
            raise UpdateError(
                f"refusing to downgrade from v{current} to {target.ref}; use the manual recovery runbook"
            )
        print(
            f"v{current} is ahead of the newest release ({target.ref}): nothing to update to. "
            f"Expected on a pre-release; the next published release will be picked up."
        )
        return True
    if _version_tuple(target.version) == _version_tuple(current):
        print("NeXgen Engine is already at the requested released version.")
        return True
    return False


def _present_notes(engine_repo: Path, current: str, target: update_channel.TargetPlan) -> TagVerdict:
    """Shows what the target carries and returns the signature verdict that admits it."""
    if target.release:
        print("\nRelease notes:\n")
        print(_changelog_between(engine_repo, current, target.ref))
        verdict = _release_verdict(engine_repo, target.ref)
    else:
        print(f"\nCommits carried by {target.label}:\n")
        print(_commits_before(engine_repo, target.rev))
        # The signature was judged when the target was chosen, against the same
        # anchor a release uses. Judging it again here could only produce a
        # second answer to a question already answered.
        verdict = target.channel_target.verdict  # type: ignore[union-attr]
    if verdict.ok:
        print(f"\nRelease signature: verified ({verdict.detail}).")
    else:
        print(f"\nWARNING: the signature of {target.ref} could not be verified: {verdict.detail}.", file=sys.stderr)
    return verdict


def _gate_on_signature(args: argparse.Namespace, target: update_channel.TargetPlan, verdict: TagVerdict) -> None:
    """The release policy on an unverified signature, or nothing when it is verified.

    Installing without a check happens in exactly two cases: the machine has no
    verifier program at all (BAD and UNTRUSTED already refused inside
    _release_verdict, in every mode), or the installer said so with
    --allow-unverified. An unsigned or otherwise unverifiable release without
    the flag refuses below: anyone able to publish an unsigned tag must not be
    able to install code on a whole fleet of unattended machines.
    """
    if verdict.ok:
        return
    if args.unattended and not verdict.no_verifier:
        raise UpdateError(
            f"unattended update refuses {target.ref}: its signature could not be verified "
            f"({verdict.detail}); recover by running 'nexgen-update --target {target.ref.removeprefix('v')}' "
            "--allow-unverified interactively once you have checked the release"
        )
    if args.allow_unverified or verdict.no_verifier:
        print(f"\nNote: the signature of {target.ref} could not be checked on this machine; installing anyway.")
        return
    raise UpdateRefused(
        f"release {target.ref} could not be verified ({verdict.detail}). "
        "Nothing was installed. Rerun with --allow-unverified only after checking the release by hand."
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    input_fn: Callable[[str], str] = input,
) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    if args.set_unattended_ceiling:
        state_dir = _state_dir(env)
        state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(state_dir / CEILING_FILE, args.set_unattended_ceiling + "\n")
        print(f"Unattended updates on this machine now take up to a {args.set_unattended_ceiling} release.")
        return 0
    previous_head = ""
    engine_repo: Path | None = None
    pin_file: Path | None = None
    mutation_started = False
    try:
        engine_repo, data_repo = resolve_repositories(env)
        entry = _tree_entry(engine_repo)
        split_topology = data_repo != engine_repo
        state_dir = _state_dir(env)
        current = _current_version(engine_repo)
        target = update_channel.plan_target(
            engine_repo, state_dir, channel=args.channel, requested=args.target, vault_data=data_repo,
        )
        _announce(engine_repo, data_repo, current, target)
        if _settled(engine_repo, current, target, requested=args.target):
            return 0

        if args.unattended and target.release:
            # The ceiling caps an unattended *release* jump: a person has not
            # read the notes. A dev channel is a different opt-in, made
            # deliberately and remembered, and its target is proved (signed, and
            # for dev-verified green on both platforms) before it gets here.
            _assert_within_unattended_ceiling(current, target.ref, _read_ceiling(state_dir))
        if args.unattended:
            if _read_rejected(state_dir).get("target") == target.ref:
                print(
                    f"{target.ref} was rolled back after a failed unattended update; unattended updates skip it. "
                    f"Review it, then run 'nexgen-update --target {target.ref.removeprefix('v')}' to try again."
                )
                return 0

        verdict = _present_notes(engine_repo, current, target)

        if args.check:
            print("\nCheck only. No installed files or branch were changed.")
            return 0

        _gate_on_signature(args, target, verdict)

        _assert_mergeable(engine_repo, data_repo, target.ref, split_topology=split_topology)

        pin_candidate = data_repo / "99-INDEX" / "ENGINE-PIN.txt"
        # In a split topology the pin is this machine's migration record:
        # create it when missing instead of silently leaving the fleet
        # unpinned (a "success" that diverges the next alignment).
        pin_file = pin_candidate if split_topology else None
        print("\nPlan:")
        merge_mode = "--ff-only" if split_topology else "--no-edit"
        print(f"  1. git merge {merge_mode} {target.label}")
        step = 2
        if pin_file:
            print(f"  {step}. update 99-INDEX/ENGINE-PIN.txt through vault-push")
            step += 1
        print(f"  {step}. agent-sync apply")
        step += 1
        print(f"  {step}. agent-doctor --summary")

        if not (args.yes or args.unattended) and not _confirm(input_fn, current=current, target=target.label):
            print("Update cancelled. No installed files or branch were changed.")
            return 0

        pre_fail, pre_doctor_rc = _doctor(entry, data_repo=data_repo)
        if pre_fail is None:
            raise UpdateError("pre-upgrade doctor did not return a readable FAIL summary")
        if pre_doctor_rc != 0 and pre_fail == 0:
            raise UpdateError("pre-upgrade doctor returned an inconsistent result")
        lock = host_mutation("nexgen-update", state_dir=state_dir, timeout=UPDATE_LOCK_WAIT_SECONDS)
        try:
            lock.acquire()
        except LockTimeoutError:
            if args.unattended:
                print("Another sync is running on this machine; the unattended update is retried on the next beat.")
                return 0
            print("nexgen-update: another sync is running on this machine; retry in a minute.", file=sys.stderr)
            return EXIT_BUSY_MANUAL
        try:
            # Whatever the guard did while we waited for the lock or for an
            # answer at the prompt, the merge needs the state checked above.
            # The lock covers ONLY the merge: pin (vault-push) and apply run
            # as child processes that take the same host lock themselves.
            # Holding it across them would deadlock (lock is not re-entrant
            # across processes, see lock.host_mutation). A guard cycle racing
            # between merge and apply is resolved by re-running apply.
            _assert_mergeable(engine_repo, data_repo, target.ref, split_topology=split_topology)
            previous_head = _git(engine_repo, "rev-parse", "HEAD").stdout.strip()
            mutation_started = True
            merge = _git(engine_repo, "merge", merge_mode, target.ref, check=False)
            if merge.returncode != 0:
                _git(engine_repo, "merge", "--abort", check=False)
                ref_moved = _git(engine_repo, "rev-parse", "HEAD").stdout.strip() != previous_head
                detail = (merge.stderr or merge.stdout or "merge failed").strip()
                if ref_moved:
                    raise PostMergeError(
                        f"merge {merge_mode} {target.label} failed after the engine ref moved: {detail}",
                        previous_head=previous_head, engine_repo=engine_repo,
                    )
                _assert_clean(engine_repo, label="engine after failed merge")
                if _git(engine_repo, "rev-parse", "--quiet", "--verify", "MERGE_HEAD", check=False).returncode == 0:
                    raise UpdateError("failed merge cleanup left an active merge")
                mutation_started = False
                raise UpdateError(
                    f"merge {merge_mode} {target.label}: {detail}\n"
                    f"The merge was rolled back; {engine_repo} is still at {previous_head[:9]}."
                )
        finally:
            lock.release()
        if merge.stdout:
            print(merge.stdout.rstrip())

        if pin_file:
            try:
                _commit_split_pin(
                    pin_file=pin_file,
                    target_head=target.rev,
                    target=target.label,
                    data_repo=data_repo,
                    entry=entry,
                )
            except UpdateError as exc:
                raise PostMergeError(
                    f"engine tag merged, but the private engine pin was not published: {exc}",
                    previous_head=previous_head,
                    engine_repo=engine_repo,
                ) from exc

        provision = _run([*entry, "apply"], cwd=data_repo, check=False, capture=False)
        if provision.returncode != 0:
            raise PostMergeError(
                "engine tag merged, but agent-sync apply failed",
                previous_head=previous_head,
                engine_repo=engine_repo,
            )

        post_fail, post_doctor_rc = _doctor(entry, data_repo=data_repo)
        if post_fail is None:
            raise PostMergeError(
                "engine tag merged, but the final doctor did not return a readable summary",
                previous_head=previous_head,
                engine_repo=engine_repo,
            )
        if post_doctor_rc != 0 and post_fail == 0:
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
        if installed != target.version:
            raise PostMergeError(
                f"VERSION is {installed}, expected {target.version}",
                previous_head=previous_head,
                engine_repo=engine_repo,
            )
        _clear_rejected(state_dir)
        if post_fail == 0:
            print(f"\nNeXgen Engine {target.label} installed: doctor reports no failures.")
            print("Warnings listed above (if any) are non-blocking: unprovisioned")
            print("optional deps provision on first use, orphans stay untouched by design.")
        else:
            print(f"\nNeXgen Engine {target.label} installed with no new doctor failures.")
            print(f"The machine still has {post_fail} pre-existing doctor failure(s).")
        print("Other machines remain behind until they run nexgen-update too.")
        print(
            "Cloud-Server note: the VPS is a separate install; follow "
            "03-INFRA/deploy/README.md before redeploying it."
        )
        return 0
    except update_channel.ChannelError as exc:
        # A channel that cannot answer, or that answered "nothing installable",
        # is a refusal this machine's owner can act on, not a crash. A refused
        # signature keeps the exit code a refused release has (70).
        print(f"nexgen-update: ERROR: {exc}", file=sys.stderr)
        return EXIT_REFUSED if isinstance(exc, update_channel.ChannelRefused) else 1
    except (UpdateError, OSError, UnicodeError, subprocess.SubprocessError,
            KeyboardInterrupt) as exc:
        safe_error = exc if isinstance(exc, UpdateError) else UpdateError(f"update operation failed ({type(exc).__name__})")
        if mutation_started and previous_head and engine_repo is not None:
            interrupted = exc if isinstance(exc, PostMergeError) else PostMergeError(
                f"update interrupted after the merge was started: {safe_error}",
                previous_head=previous_head, engine_repo=engine_repo,
            )
            return _recover_failed_update(
                interrupted, args=args, entry=entry, data_repo=data_repo, pin_file=pin_file,
                state_dir=state_dir, target=target, current=current,
            )
        if isinstance(exc, KeyboardInterrupt):
            print("nexgen-update: interrupted before anything was changed.", file=sys.stderr)
            return 130
        print(f"nexgen-update: ERROR: {safe_error}", file=sys.stderr)
        return exc.exit_code if isinstance(exc, UpdateRefused) else 1


class EngineUpdater:
    """Support class for invoking the updater programmatically."""

    @staticmethod
    def main(argv: Sequence[str] | None = None, **kwargs: Any) -> int:
        return main(argv, **kwargs)

    @staticmethod
    def check_updates() -> tuple[bool, str, str]:
        """Checks whether new released versions exist, without applying them.

        Raises on failure (offline, broken remote, missing ref): callers must
        show that, never a quiet "no update" that leaves the machine behind.
        """
        engine_repo, _ = resolve_repositories(os.environ)
        curr = _current_version(engine_repo)
        tags = update_channel.released_tags(engine_repo)
        if tags:
            latest = tags[0].removeprefix("v")
            has_up = _version_tuple(latest) > _version_tuple(curr)
            return has_up, f"v{curr}", f"v{latest}"
        return False, f"v{curr}", f"v{curr}"


if __name__ == "__main__":
    raise SystemExit(main())
