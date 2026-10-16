"""Complexity score of a change. At or above the threshold it needs an independent review."""
from __future__ import annotations

import re
from fnmatch import fnmatch

from .config import Config
from .gitutil import Change
from .violations import CODE_EXT, SHELL_EXT, line_count

DEPENDENCY_FILES = re.compile(r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.py|package\.json|go\.mod|"
                              r"Cargo\.toml|Gemfile|composer\.json|pom\.xml|build\.gradle[^/]*)$")
CONTROL_FILES = re.compile(r"(^|/)(\.nexgen-discipline|\.pre-commit|\.husky/)")
WORKFLOW = re.compile(r"(^|/)\.github/workflows/")
COUNTED = CODE_EXT + SHELL_EXT + (".yml", ".yaml", ".toml", ".json")  # prose and notes are free
LINES_PER_POINT = 50
MAX_LINE_POINTS = 6
MAX_FILE_POINTS = 6


def added_lines(changes: list[Change]) -> int:
    return sum(max(line_count(c.new) - line_count(c.old), 0) for c in changes if c.path.lower().endswith(COUNTED))


def score(changes: list[Change]) -> tuple[int, list[str]]:
    reasons: list[tuple[int, str]] = []
    if (points := min(added_lines(changes) // LINES_PER_POINT, MAX_LINE_POINTS)):
        reasons.append((points, f"{added_lines(changes)} net lines added"))
    new_code = [c.path for c in changes if c.status == "A" and c.path.lower().endswith(CODE_EXT)]
    if new_code:
        reasons.append((min(2 * len(new_code), MAX_FILE_POINTS), f"{len(new_code)} new code files"))
    new_shell = [c.path for c in changes if c.status == "A" and c.path.lower().endswith(SHELL_EXT)]
    if new_shell:
        reasons.append((2 * len(new_shell), f"{len(new_shell)} new shell/PowerShell files"))
    if any(DEPENDENCY_FILES.search(c.path) for c in changes):
        reasons.append((3, "dependency manifest touched"))
    if any(CONTROL_FILES.search(c.path) for c in changes):
        reasons.append((2, "CI, hooks or discipline config touched"))
    if len({c.path.split("/")[0] if "/" in c.path else "." for c in changes}) > 3:
        reasons.append((2, "touches more than 3 top-level areas"))
    return sum(p for p, _ in reasons), [f"+{p} {why}" for p, why in reasons]


def is_complex(changes: list[Change], cfg: Config) -> tuple[bool, int, list[str]]:
    """Only changes to declared production paths can require a review: reviews cost money."""
    scoped = [c for c in changes if any(fnmatch(c.path, g) for g in cfg.review_paths)
              and not any(fnmatch(c.path, g) for g in cfg.review_exempt)]
    control = [c for c in changes if CONTROL_FILES.search(c.path) or (
        WORKFLOW.search(c.path) and "discipline" in f"{c.old}{c.new}")] if cfg.review_paths else []
    if not scoped and not control:
        return False, 0, ["no declared production path touched: no review required"]
    total, reasons = score(scoped)
    if control:  # switching the rules or the CI off must never be a way to skip the review
        total = max(total, cfg.complexity_threshold)
        reasons.append("always needs a decision: discipline config, hooks or the CI that runs the gate changed")
    return total >= cfg.complexity_threshold, total, reasons
