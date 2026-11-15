"""What this machine's recent harness turns cost, read for `nexgen openclaw doctor`.

One job: hand the OpenClaw doctor the two spend lines -- the seed a session opened with
and the calls it then made -- from local transcripts only, or say nothing when nothing
was measured.

Why it is its own file. These two are the same bill read twice, and they are the only
checks in that doctor which read a transcript instead of the configuration in force.
Keeping them here gives the two commands one owner: `nexgen openclaw doctor` asks for
them, and the scheduled `nexgen doctor` runs the same two checks itself, so the wording
and the ceilings cannot drift apart.

Cost: no model call, no network, nothing that starts the gateway. One file read per
configured agent, bounded by the tail window `turn_budget.py` owns.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nexgen_core.checks.context_budget_checks import check_context_budget
from nexgen_core.checks.turn_budget_checks import check_turn_budget
from nexgen_core.report import CheckOutcome


def outcomes(
    home_dir: Path, config: Mapping[str, Any], values: Mapping[str, str],
) -> list[CheckOutcome]:
    """The seed and the turn readings that exist for this machine, in that order.

    `openclaw.context_budget` judges the first prompt, `openclaw.turn_budget` judges the
    heaviest recent turn of every configured agent, because a session can pass at 78,717
    tokens and still spend 74 calls of it (`docs/proposals/daru-burn.md`). A reading that
    measured nothing is left out rather than printed as a permanent "could not be
    verified": a machine that did not chat through a harness today has nothing wrong.
    """
    readings = [
        check_context_budget(home_dir, env=values),
        check_turn_budget(home_dir, config=config, env=values),
    ]
    return [outcome for outcome in readings if outcome is not None]
