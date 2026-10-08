"""Git checks for one development branch and release merges into main."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from nexgen_core.git_ops import run_git

INTEGRATION_REF = "developer"


def resolve_revision(repo: Path, ref: str) -> str | None:
    """Resolve local refs and remote-tracking refs in fresh CI clones."""
    for candidate in (ref, f"origin/{ref}"):
        result = run_git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}")
        if result.returncode == 0:
            return result.stdout.strip()
    return None


def guarded_ref(branch: str) -> bool:
    return branch == "main"


def check_ref(
    repo: Path,
    ref: str,
    integration: str = INTEGRATION_REF,
    *,
    tip: str | None = None,
    base: str | None = None,
    first_push_base: str | None = None,
) -> tuple[bool, list[str]]:
    """Developer allows development commits; main accepts release merges."""
    if ref not in ("main", integration):
        return False, [f"branch '{ref}' is retired or unsupported; develop only on {integration}"]
    target = resolve_revision(repo, tip or ref)
    if target is None:
        return False, [f"could not resolve {tip or ref}"]
    if base == "0" * 40:
        if first_push_base is None:
            return False, ["first push requires an explicit published base; pass --first-push-base"]
        base = first_push_base
    previous = resolve_revision(repo, base or f"origin/{ref}")
    if previous is None:
        return False, [f"could not resolve the previous {ref} tip; pass --base"]
    if run_git(repo, "merge-base", "--is-ancestor", previous, target).returncode != 0:
        return False, [f"{ref} does not descend from its previous tip"]
    if ref == integration:
        published = resolve_revision(repo, "origin/main") or resolve_revision(repo, "main")
        if published is None:
            return False, ["could not resolve published main; fetch origin/main"]
        if run_git(repo, "merge-base", "--is-ancestor", published, target).returncode != 0:
            return False, [f"{integration} is behind published main; merge main into {integration}"]
        return True, []
    if target == previous:
        return True, []
    integrated = resolve_revision(repo, integration)
    if integrated is None:
        return False, [f"could not resolve integration ref {integration}"]
    log = run_git(repo, "log", "--first-parent", "--format=%H%x09%P%x09%s", f"{previous}..{target}")
    if log.returncode != 0:
        return False, [f"could not inspect {ref}"]
    problems = []
    for line in log.stdout.splitlines():
        commit, parents, subject = line.split("\t", 2)
        parents = parents.split()
        if len(parents) != 2:
            problems.append(f"direct commit on main: {commit[:9]} {subject}")
        elif run_git(repo, "merge-base", "--is-ancestor", parents[1], integrated).returncode != 0:
            problems.append(f"main merge {commit[:9]} did not come from {integration}")
    return not problems, problems


def sync_developer(repository: str) -> int:
    """Ask GitHub to merge main atomically; conflicts never rewrite developer.

    The API serializes the merge against the live branch tips. No local force
    push or temporary branch can discard development that arrived meanwhile.
    GitHub creates the merge commit, retaining its verified signing identity.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
        print("Repository GitHub non valido.", file=sys.stderr)
        return 1
    try:
        result = subprocess.run(
            ["gh", "api", "--method", "POST", f"repos/{repository}/merges",
             "-f", "base=developer", "-f", "head=main",
             "-f", "commit_message=Merge main into developer"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("Riallineamento non verificato. Controlla il workflow sync-developer su GitHub.", file=sys.stderr)
        return 1
    if result.returncode:
        print("Riallineamento fermato. Controlla il merge main verso developer su GitHub.", file=sys.stderr)
        return 1
    if result.stdout.strip():
        try:
            merged = json.loads(result.stdout)
            sha = merged["sha"]
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ValueError("invalid merge sha")
        except (ValueError, KeyError, TypeError):
            print("Risposta del merge non verificabile. Controlla developer su GitHub.", file=sys.stderr)
            return 1
        print(f"Developer riallineato a main: {sha[:12]}.")
    else:
        print("Developer include gia main.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    parser.add_argument("--integration", default=INTEGRATION_REF)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--tip", help="snapshot to check (CI uses HEAD)")
    parser.add_argument("--base", help="previous tip, or the PR's target commit")
    parser.add_argument("--first-push-base", help="published base for a branch's first push")
    parser.add_argument("--sync-developer", metavar="OWNER/REPO", help="merge main through the GitHub API")
    args = parser.parse_args(argv)
    if args.sync_developer:
        return sync_developer(args.sync_developer)
    if not args.ref:
        parser.error("--ref is required unless --sync-developer is used")
    ok, problems = check_ref(
        Path(args.repo), args.ref, args.integration, tip=args.tip, base=args.base,
        first_push_base=args.first_push_base,
    )
    if ok:
        print(f"lane-guard: {args.ref} honors the developer/main contract.")
        return 0
    for problem in problems:
        print(f"lane-guard: {problem}", file=sys.stderr)
    return 1
