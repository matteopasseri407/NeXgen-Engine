#!/usr/bin/env python3
"""Lane guard: the machine-enforced half of the agent-lane contract.

Fails when a release (or history) ref breaks the workflow:

1. the tip must descend from the integration branch (`developer`), and
2. every non-merge commit on top of it must be a `release:` chore.

Anything else means work bypassed the lane (direct fix commit), and the
push/merge must move to a `dev/<agent>` lane instead. Stdlib only, so CI
and any checkout can run it: ``python3 lane_guard.py --ref <branch>``.
Exit 0 when the ref honors the contract, 1 with the offending commits
listed when it does not.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: Commits allowed straight on release rungs: version chores only.
RELEASE_CHORE_RE = re.compile(r"^(release:|Release v|chore\(release\))")

INTEGRATION_REF = "developer"
GUARDED_PREFIXES = ("release/",)
GUARDED_EXACT = ("main",)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=False,
    )


def guarded_ref(branch: str) -> bool:
    """True when the lane contract guards this ref at all."""
    return branch in GUARDED_EXACT or branch.startswith(GUARDED_PREFIXES)


def check_ref(repo: Path, ref: str, integration: str = INTEGRATION_REF) -> tuple[bool, list[str]]:
    """(ok, problems) for one guarded ref. Empty problems when ok."""
    problems: list[str] = []
    if _git(repo, "merge-base", "--is-ancestor", integration, ref).returncode != 0:
        problems.append(
            f"{ref} does not descend from {integration}: "
            f"advance {ref} from {integration}, never sideways."
        )
        return False, problems
    log = _git(repo, "rev-list", "--no-merges", "--format=%H %s", f"{integration}..{ref}")
    if log.returncode != 0:
        problems.append(f"could not list commits on {ref}: {log.stderr.strip()}")
        return False, problems
    offenders: list[str] = []
    current: str = ""
    for line in log.stdout.splitlines():
        if line.startswith("commit "):
            current = line.split(" ", 1)[1] if " " in line else ""
            continue
        subject = line.strip()
        if not subject or RELEASE_CHORE_RE.match(subject):
            continue
        offenders.append(f"{current[:9]} {subject}")
    if offenders:
        problems.append(
            f"{len(offenders)} non-chore commit(s) straight on {ref} "
            f"(move them to a dev/<agent> lane):"
        )
        problems.extend(f"  - {item}" for item in offenders)
    return (not problems), problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lane-guard", description=__doc__)
    parser.add_argument("--ref", required=True, help="branch ref to check (e.g. release/v2.3.10)")
    parser.add_argument("--integration", default=INTEGRATION_REF)
    parser.add_argument("--repo", default=".", help="engine checkout to inspect")
    args = parser.parse_args(argv)

    if not guarded_ref(args.ref):
        print(f"lane-guard: {args.ref} is not a guarded rung; nothing to enforce.")
        return 0
    ok, problems = check_ref(Path(args.repo), args.ref, args.integration)
    if ok:
        print(f"lane-guard: {args.ref} honors the lane contract.")
        return 0
    print(f"lane-guard: {args.ref} breaks the lane contract:", file=sys.stderr)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
