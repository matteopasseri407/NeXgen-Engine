"""Run every gate over one scope and render the outcome for an agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import regress, review
from .config import COMMAND, Config
from .gitutil import STAGED, Change, own_changes
from .score import is_complex
from .integrity import check_test_integrity
from .shellgate import check_shell
from .sizes import check_sizes
from .swallow import check_swallowed
from .violations import Violation


@dataclass
class Outcome:
    violations: list[Violation] = field(default_factory=list)
    complex: bool = False
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    receipt_problem: str | None = None
    gated: bool = False

    @property
    def ok(self) -> bool:
        return not self.violations and not self.receipt_problem


def static_checks(changed: list[Change], cfg: Config) -> list[Violation]:
    return (check_sizes(changed, cfg) + check_shell(changed, cfg)
            + check_test_integrity(changed, cfg) + check_swallowed(changed, cfg))


def release_checks(changed: list[Change], cfg: Config) -> list[Violation]:
    """What a release still judges: the shape of the code it ships.

    The size and test-integrity ratchets judge each change once, at the push
    that brings it in. A release diff spans every push since the last release,
    including growth from before the gate existed, so re-judging it there only
    blocks the release without protecting anything a push did not already check.
    """
    return check_shell(changed, cfg) + check_swallowed(changed, cfg)


def run(root: Path, cfg: Config, base: str, head: str | None, need_review: bool,
        release: bool = False) -> Outcome:
    changed = own_changes(root, base, head)
    out = Outcome(violations=(release_checks if release else static_checks)(changed, cfg))
    out.complex, out.score, out.reasons = is_complex(changed, cfg)
    out.gated = need_review
    if need_review and head != STAGED:  # per-commit proof needs committed work; a release PR is static-only
        out.violations += regress.check_commits(root, cfg, base, head or "HEAD")
    if need_review and out.complex:
        out.receipt_problem = review.receipt_problem(root, review.diff_hash(root, base, head), head)
    return out


def render(out: Outcome) -> str:
    lines = [f"FAIL {v}" for v in out.violations]
    if out.receipt_problem:
        lines.append(f"FAIL complex change (score {out.score}: {'; '.join(out.reasons)}): {out.receipt_problem}. "
                     f"Run `{COMMAND} review`, get the owner's go-ahead for the reviewer, "
                     f"then `{COMMAND} receipt`.")
    if out.ok:
        extra = (", review receipt present" if out.gated else f", complex: `{COMMAND} gate` will need a review") if out.complex else ""
        note = f" (complexity {out.score}{extra})"
        lines.append("OK" + note)
    return "\n".join(lines)
