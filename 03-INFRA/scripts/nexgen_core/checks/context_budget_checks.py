"""Doctor check for OpenClaw first-prompt context budget.

nexgen: one-job reason="verify that recent OpenClaw harness sessions stayed within the first-prompt token budget"
One job: read recent local harness session transcripts started by OpenClaw and verify
that their initial prompt tokens did not exceed the context budget.

A machine that did not chat through OpenClaw in the look-back window has nothing wrong
with it, so the check says nothing then: a permanent "could not be verified" line is one
more row a person learns to skip, and `nexgen doctor --strict` fails on it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from nexgen_core.i18n import t
from nexgen_core.openclaw.context_budget import (
    SessionReading,
    claude_home,
    find_recent_claude_sessions,
    find_recent_codex_sessions,
    get_first_prompt_budget,
)
from nexgen_core.paths import codex_home
from nexgen_core.report import CheckOutcome, Severity

#: The doctor check identifier.
CHECK_ID = "openclaw.context_budget"


def _resolve_dirs(home: Path, claude_dir: Path | None, codex_dir: Path | None) -> tuple[Path, Path]:
    """The two folders the harnesses really write to, honouring their own overrides."""
    return (
        claude_dir if claude_dir is not None else claude_home(home),
        codex_dir if codex_dir is not None else codex_home(home),
    )


def check_context_budget(
    home: Path,
    *,
    claude_dir: Path | None = None,
    codex_dir: Path | None = None,
    budget: int | None = None,
    now: float | None = None,
    env: Mapping[str, str] | None = None,
) -> CheckOutcome | None:
    """Judge recent OpenClaw harness sessions against the first-prompt token budget, or say nothing.

    None means there was nothing to judge: no OpenClaw-started transcript in the window
    is the normal state of a healthy machine that did not use OpenClaw that day.
    """
    target_budget = budget if budget is not None else get_first_prompt_budget(env)
    c_dir, cx_dir = _resolve_dirs(home, claude_dir, codex_dir)

    claude_sessions = find_recent_claude_sessions(c_dir, now=now)
    codex_sessions = find_recent_codex_sessions(cx_dir, now=now)
    all_sessions: list[SessionReading] = [*claude_sessions, *codex_sessions]

    if not all_sessions:
        return None

    over_budget = [s for s in all_sessions if s.tokens > target_budget]
    if over_budget:
        worst = max(over_budget, key=lambda s: s.tokens)
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t(
                "OpenClaw session in '{slug}' started over context budget: {tokens:,} tokens (budget: {budget:,}).",
                slug=worst.slug,
                tokens=worst.tokens,
                budget=target_budget,
            ),
            action=t("Run 'nexgen openclaw setup', then 'nexgen openclaw doctor'."),
            detail=f"{worst.slug}: {worst.tokens:,} tokens",
        )

    largest = max(all_sessions, key=lambda s: s.tokens)
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=t(
            "First prompt context budget OK (largest seen: {tokens:,} tokens, budget: {budget:,}).",
            tokens=largest.tokens,
            budget=target_budget,
        ),
        detail=f"largest: {largest.tokens:,} tokens",
    )
