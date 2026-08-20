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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexgen-release",
        description="The release rules, callable from CI and from the command line.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check-version", help="Do VERSION and the tag agree?")
    p.add_argument("version")
    p.add_argument("tag")

    p = sub.add_parser("scan-range", help="Which commit range needs to be scanned")
    p.add_argument("--event", required=True)
    p.add_argument("--before", default="")
    p.add_argument("--sha", required=True)
    p.add_argument("--pr-base", default="")
    p.add_argument("--pr-head", default="")

    args = parser.parse_args(argv)

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
