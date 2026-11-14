"""Doctor check for OpenClaw first-prompt context budget.

nexgen: one-job reason="verify that recent OpenClaw harness sessions stayed within the first-prompt token budget"
One job: read recent local harness session transcripts started by OpenClaw and verify
that their initial prompt tokens did not exceed the context budget.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from nexgen_core.i18n import t
from nexgen_core.openclaw.context_budget import (
    SessionReading,
    find_recent_claude_sessions,
    find_recent_codex_sessions,
    get_first_prompt_budget,
)
from nexgen_core.report import CheckOutcome, Severity

#: The doctor check identifier.
CHECK_ID = "openclaw.context_budget"


def _resolve_claude_dir(home: Path, override: Path | None) -> Path:
    """The Claude configuration directory to examine."""
    return override if override is not None else (home / ".claude")


def _resolve_codex_dir(home: Path, override: Path | None) -> Path:
    """The Codex configuration directory to examine."""
    return override if override is not None else (home / ".codex")


def check_context_budget(
    home: Path,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
    *,
    claude_dir: Path | None = None,
    codex_dir: Path | None = None,
    budget: int | None = None,
    now: float | None = None,
    env: Mapping[str, str] | None = None,
) -> CheckOutcome:
    """Judge recent OpenClaw harness sessions against the first-prompt token budget."""
    target_budget = budget if budget is not None else get_first_prompt_budget(env)
    c_dir = _resolve_claude_dir(home, claude_dir)
    cx_dir = _resolve_codex_dir(home, codex_dir)

    claude_sessions = find_recent_claude_sessions(c_dir, now=now)
    codex_sessions = find_recent_codex_sessions(cx_dir, now=now)
    all_sessions: list[SessionReading] = [*claude_sessions, *codex_sessions]

    if not all_sessions:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.UNDETERMINED,
            message=t("No OpenClaw session transcript measured in the look-back window."),
        )

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
