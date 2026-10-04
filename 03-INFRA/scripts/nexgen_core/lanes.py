"""Git-only checks for contributor lanes; no runtime alignment or host writes."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

RELEASE_CHORE_RE = re.compile(r"^(release:|Release v|chore\(release\))")
INTEGRATION_REF = "developer"

#: The only working branches agents may commit on. Anything else (feat/*,
#: fix/*, draft/*, per-fix throwaways) is rejected by the guard: one durable
#: lane per agent, no branch casino.
LANE_BRANCH_RE = re.compile(r"^dev/[A-Za-z0-9][A-Za-z0-9._-]*$")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def resolve_revision(repo: Path, ref: str) -> str | None:
    """Resolve a local ref, or its remote-tracking counterpart in a CI clone."""
    for candidate in (ref, f"origin/{ref}"):
        result = _git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}")
        if result.returncode == 0:
            return result.stdout.strip()
    return None


def guarded_ref(branch: str) -> bool:
    return branch in ("main", "developer") or branch.startswith("release/")


def check_ref(
    repo: Path,
    ref: str,
    integration: str = INTEGRATION_REF,
    *,
    tip: str | None = None,
    base: str | None = None,
    first_push_base: str | None = None,
) -> tuple[bool, list[str]]:
    """Check a snapshot, never rewrite it. Developer requires a known base."""
    if not guarded_ref(ref):
        if LANE_BRANCH_RE.match(ref) is not None:
            return True, []
        return False, [
            f"branch '{ref}' is not a dev/<agent> lane; "
            "work on your dev/<agent> lane instead (no feat/*, fix/*, draft/*, per-fix branches)"
        ]
    target = resolve_revision(repo, tip or ref)
    if target is None:
        return False, [f"could not resolve {tip or ref}"]
    if ref in ("developer", "main"):
        if base == "0" * 40:
            if first_push_base is None:
                return False, ["first push requires an explicit published base; pass --first-push-base"]
            base = first_push_base
        previous = resolve_revision(repo, base or f"origin/{ref}")
        if previous is None:
            return False, [f"could not resolve the previous {ref} tip; pass --base"]
        if _git(repo, "merge-base", "--is-ancestor", previous, target).returncode != 0:
            return False, [f"{ref} does not descend from its previous tip"]
        if ref == "main":
            return (True, []) if target == previous else (False, ["main is frozen history; integrate on developer"])
        log = _git(repo, "log", "--first-parent", "--format=%H%x09%P%x09%s", f"{previous}..{target}")
        if log.returncode != 0:
            return False, [f"could not inspect {ref}: {log.stderr.strip()}"]
        offenders = []
        for line in log.stdout.splitlines():
            commit, parents, subject = line.split("\t", 2)
            if len(parents.split()) < 2:
                offenders.append(f"{commit[:9]} {subject}")
        return not offenders, [f"direct commit on developer: {entry}" for entry in offenders]
    integrated = resolve_revision(repo, integration)
    if integrated is None:
        return False, [f"could not resolve integration ref {integration}"]
    if _git(repo, "merge-base", "--is-ancestor", integrated, target).returncode != 0:
        return False, [f"{ref} does not descend from {integration}; advance it from developer"]
    log = _git(repo, "log", "--no-merges", "--format=%H%x09%s", f"{integrated}..{target}")
    if log.returncode != 0:
        return False, [f"could not inspect {ref}: {log.stderr.strip()}"]
    offenders = []
    for line in log.stdout.splitlines():
        commit, subject = line.split("\t", 1)
        if not RELEASE_CHORE_RE.match(subject):
            offenders.append(f"{commit[:9]} {subject}")
    return not offenders, [f"non-release commit on {ref}: {entry}" for entry in offenders]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--integration", default=INTEGRATION_REF)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--tip", help="snapshot to check (CI uses HEAD)")
    parser.add_argument("--base", help="previous tip, or the PR's target commit")
    parser.add_argument("--first-push-base", help="published base when the push's previous tip is all zeros")
    args = parser.parse_args(argv)
    ok, problems = check_ref(
        Path(args.repo), args.ref, args.integration, tip=args.tip, base=args.base,
        first_push_base=args.first_push_base,
    )
    if ok:
        print(f"lane-guard: {args.ref} honors the lane contract.")
        return 0
    for problem in problems:
        print(f"lane-guard: {problem}", file=sys.stderr)
    return 1
