"""Run every gate over one scope and render the outcome for an agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import regress
from .config import Config
from .gitutil import STAGED, Change, own_changes
from .integrity import check_test_integrity
from .shellgate import check_shell
from .sizes import split_queue
from .swallow import check_swallowed
from .violations import Violation


@dataclass
class Outcome:
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)  # the split queue: shown, never blocking

    @property
    def ok(self) -> bool:
        return not self.violations


def static_checks(changed: list[Change], cfg: Config) -> list[Violation]:
    """What refuses a push: shell/PowerShell, test integrity, swallowed errors."""
    return (check_shell(changed, cfg) + check_test_integrity(changed, cfg)
            + check_swallowed(changed, cfg))


def size_alarm(changed: list[Change], cfg: Config) -> list[Violation]:
    """The size ceiling over this change: printed as a warning, never a refusal."""
    return split_queue(changed, cfg)


def release_checks(changed: list[Change], cfg: Config) -> list[Violation]:
    """What a release still judges: the shape of the code it ships.

    The shell and swallowed-error gates judge each change once, at the push that
    brings it in, but a release diff spans every push since the last release and
    is the last chance to refuse the code it ships. The size ceiling is not
    re-judged there: it is an alarm every push already printed, and repeating
    the whole accumulated queue would bury the release's own failures.
    """
    return check_shell(changed, cfg) + check_swallowed(changed, cfg)


def run(root: Path, cfg: Config, base: str, head: str | None, per_commit: bool,
        release: bool = False) -> Outcome:
    """Everything one scope is judged by.

    The score and the review receipts are switched off: nothing here asks for a
    review. `per_commit` adds the proofs that need committed work; the size
    ceiling is collected as a warning whatever happens.
    """
    changed = own_changes(root, base, head)
    out = Outcome(violations=(release_checks if release else static_checks)(changed, cfg),
                  warnings=[] if release else size_alarm(changed, cfg))
    if per_commit and head != STAGED:  # per-commit proof needs committed work; a release PR is static-only
        out.violations += regress.check_commits(root, cfg, base, head or "HEAD")
    return out


def render(out: Outcome) -> str:
    lines = [f"FAIL {v}" for v in out.violations]
    lines += [f"WARN {v}" for v in out.warnings]
    if out.ok:
        queued = f" ({len(out.warnings)} in the split queue)" if out.warnings else ""
        lines.append("OK" + queued)
    return "\n".join(lines)
