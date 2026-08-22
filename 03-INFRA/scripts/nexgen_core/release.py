#!/usr/bin/env python3
"""The release rules, where they can actually be tested.

They used to live inside shell blocks nested in the CI's YAML: they decided
what the leak-prevention gate checks and whether a version is acceptable,
and the only way to verify them was to actually trigger a CI event.

A glob living in a shell is not a rule: `[0-9]*.[0-9]*.[0-9]*` accepts
`1.2.3abc` as if it were semver. Here the rule is anchored, and it's tested.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

#: Anchored semver, with optional prerelease and build. Anchored is the
#: whole point: without anchors, "almost a version" passes as a version.
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

#: The SHA GitHub sends when a branch had no "before".
ZERO_SHA = "0" * 40


def is_semver(value: str) -> bool:
    """True only if the string is a version, not merely resembles one."""
    return bool(SEMVER_RE.match(value.strip()))


def version_matches_tag(version: str, tag: str) -> tuple[bool, str]:
    """A release tag must name the version it contains."""
    version = version.strip()
    tag = tag.strip()
    if not is_semver(version):
        return False, f"VERSION is not a valid version: '{version}'"
    if f"v{version}" != tag:
        return False, (
            f"the tree at tag {tag} carries VERSION {version}: "
            f"a release tag must name the version it contains"
        )
    return True, f"{tag} and VERSION {version} agree"


def scan_range(event_name: str, before: str, sha: str, pr_base: str = "", pr_head: str = "") -> str:
    """The commit range the leak-prevention gate must examine.

    This is a security decision, not CI scaffolding: getting it wrong means
    not checking the commits a secret could have entered through.
    """
    if event_name == "pull_request":
        if not pr_base or not pr_head:
            raise ValueError("a pull request must declare base and head")
        return f"{pr_base}..{pr_head}"
    if not before or before == ZERO_SHA:
        # First push to a branch: there's no "before" to compare against.
        return sha
    return f"{before}..{sha}"


def newer_version(left: str, right: str) -> bool:
    """True if `left` is newer than `right`, comparing numbers rather than text."""
    def parts(v: str) -> tuple[int, ...]:
        core = v.strip().lstrip("v").split("-", 1)[0].split("+", 1)[0]
        return tuple(int(n) for n in core.split(".")[:3])

    return parts(left) > parts(right)


#: The marker a private maintainer tool carries. It must never reach the
#: public tree, and a release is the moment it would.
#: Assembled rather than written out, so this file does not match its own
#: search and report itself as the leak.
PRIVATE_MARKER = "NEXGEN" "-PRIVATE-" "MAINTAINER-TOOL"


def _preflight() -> int:
    """The checks a maintainer would otherwise have to remember.

    Each one has been forgotten at least once: a version that disagreed with
    its tag, launchers regenerated on one machine and not committed, a lint
    gate raised instead of respected, and — the one that matters most — a
    private tool reaching a public repository.
    """
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    problems: list[str] = []
    checked: list[str] = []

    version = (repo / "VERSION").read_text(encoding="utf-8").strip()
    if not is_semver(version):
        problems.append(f"VERSION is not a version: '{version}'")
    else:
        checked.append(f"VERSION is {version}")

    tags = subprocess.run(
        ["git", "-C", str(repo), "tag", "--list", "--sort=-v:refname"],
        capture_output=True, text=True, check=False, timeout=30,
    ).stdout.split()
    if tags:
        newest = tags[0]
        if not newer_version(version, newest):
            problems.append(
                f"VERSION {version} is not newer than the newest tag {newest}: "
                f"a release would name a version that already exists"
            )
        else:
            checked.append(f"newer than {newest}")

    from nexgen_core.legacy_launchers import (
        REMOVE_AFTER,
        expected_files,
        is_expired,
        matches,
    )

    # Compatibility with an expiry, so it cannot become permanent by
    # inertia. This does not delete anything: it refuses to tag, which puts
    # the decision in front of a person exactly once.
    if is_semver(version) and is_expired(version):
        problems.append(
            f"the legacy launchers were due to go after {REMOVE_AFTER} and this "
            f"release is {version}: either delete nexgen_core/legacy_launchers.py "
            f"and the twenty files it writes, or raise REMOVE_AFTER because a "
            f"machine is genuinely still on the old release"
        )
    else:
        checked.append(f"legacy launchers still within their window (until {REMOVE_AFTER})")

    stale = [
        path.name
        for path, content in expected_files(repo / "03-INFRA" / "scripts").items()
        if not matches(path, content)
    ]
    if stale:
        problems.append(
            "launchers out of step with their table (regenerate and commit them): "
            + ", ".join(sorted(stale))
        )
    else:
        checked.append("legacy launchers match their table")

    leaked = subprocess.run(
        ["git", "-C", str(repo), "grep", "-l", PRIVATE_MARKER],
        capture_output=True, text=True, check=False, timeout=60,
    ).stdout.split()
    if leaked:
        problems.append("private maintainer tooling reached the public tree: " + ", ".join(leaked))
    else:
        checked.append("no private tooling in the tree")

    baseline = subprocess.run(
        [sys.executable, str(repo / "03-INFRA" / "scripts" / "ruff_baseline_check.py")],
        capture_output=True, text=True, check=False, timeout=300,
    )
    if baseline.returncode != 0:
        problems.append("the lint gate does not pass; fix the findings rather than regenerating it")
    else:
        checked.append("lint gate passes")

    for line in checked:
        print(f"  ok   {line}")
    for line in problems:
        print(f"  ✗    {line}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} thing(s) to settle before tagging.", file=sys.stderr)
        return 1
    print("\nReady to tag.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexgen-release-rules",
        description="The release rules, callable from CI and from the command line.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check-version", help="Do VERSION and the tag agree?")
    p.add_argument("version")
    p.add_argument("tag")

    sub.add_parser("preflight", help="Everything a release needs to be true before tagging")

    p = sub.add_parser("scan-range", help="Which commit range needs to be scanned")
    p.add_argument("--event", required=True)
    p.add_argument("--before", default="")
    p.add_argument("--sha", required=True)
    p.add_argument("--pr-base", default="")
    p.add_argument("--pr-head", default="")

    args = parser.parse_args(argv)

    if args.command == "preflight":
        return _preflight()

    if args.command == "check-version":
        ok, message = version_matches_tag(args.version, args.tag)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    try:
        print(scan_range(args.event, args.before, args.sha, args.pr_base, args.pr_head))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
