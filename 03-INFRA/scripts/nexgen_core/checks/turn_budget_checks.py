"""Doctor check for what one OpenClaw turn spends, not only what its first prompt cost.

nexgen: one-job reason="warn when a recent OpenClaw turn spent more than the per-turn token budget"

One job: read the newest transcript of every configured agent, judge the most expensive
of its recent turns against one per-turn ceiling, and report the worst turn with its call
count so a session that is green on the first-prompt budget and still spends millions of
tokens stops hiding.

Silence when nothing was measured: a machine that did not chat through a harness in the
look-back window has nothing wrong with it, and a permanent "could not be verified" line
is one more row a person learns to skip.

The warning, not a failure: a turn over the ceiling is a cost signal, and the doctor's
verdicts are about whether the machine works. The owner decides what to do with it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from nexgen_core.i18n import t
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.context_budget import claude_home
from nexgen_core.openclaw.turn_budget import AgentTurn, agent_turns, resolved_budget
from nexgen_core.paths import openclaw_config
from nexgen_core.report import CheckOutcome, Severity

#: The doctor check identifier.
CHECK_ID = "openclaw.turn_budget"


def check_turn_budget(
    home: Path,
    *,
    config: Mapping[str, Any] | None = None,
    claude_dir: Path | None = None,
    budget: int | None = None,
    now: float | None = None,
    env: Mapping[str, str] | None = None,
) -> CheckOutcome | None:
    """Judge the heaviest recent turn of every configured agent, or say nothing.

    None means there was nothing to judge: no transcript for any agent's folder in the
    window is the normal state of a machine that did not use a harness that day.
    """
    target_budget = resolved_budget(budget, env)
    folder = claude_dir if claude_dir is not None else claude_home(home)
    if config is None:
        config, _error = read_config(openclaw_config(home, env))

    spent = agent_turns(folder, config, now=now)
    if not spent:
        return None

    worst: AgentTurn = spent[0]
    if worst.tokens > target_budget:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t(
                "Agent '{agent}' spent {tokens:,} tokens in one turn over the per-turn budget "
                "({budget:,}): {calls} model calls in session {session}.",
                agent=worst.agent,
                tokens=worst.tokens,
                budget=target_budget,
                calls=worst.calls,
                session=worst.session.name,
            ),
            action=t(
                "Lower the cost of one call before the next turn (see docs/proposals/daru-burn.md): "
                "shrink the loopback tool catalogue with a tools.byProvider deny, cap contextTokens, "
                "or move this agent to a harness lane so the turn carries its own context.",
            ),
            detail=f"{worst.agent}: {worst.tokens:,} tokens, {worst.calls} calls",
        )

    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=t(
            "Per-turn token budget OK (heaviest turn: {tokens:,} tokens in {calls} model calls, "
            "agent '{agent}', budget: {budget:,}).",
            tokens=worst.tokens,
            calls=worst.calls,
            agent=worst.agent,
            budget=target_budget,
        ),
        detail=f"{worst.agent}: {worst.tokens:,} tokens, {worst.calls} calls",
    )
