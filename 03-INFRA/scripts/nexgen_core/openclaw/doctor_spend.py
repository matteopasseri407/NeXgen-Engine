"""The spend guard and the recent-turn readings, handed to `nexgen openclaw doctor`.

One job: give the OpenClaw doctor the three lines that say what this machine can
spend -- the flat-model guard read from the Vault's `quota.yaml`, the seed a
session opened with, and the calls it then made -- from the configuration and the
local transcripts only, leaving out a reading that measured nothing.

Why it is its own file. The guard and the two readings answer one question: is this
machine about to spend, or has it already. The first is a model ref in the file,
which is a bill the next turn has not made yet; the other two are the same bill
read twice from a transcript. Keeping them here gives the two commands one owner:
`nexgen openclaw doctor` asks for them, and the scheduled `nexgen doctor` runs the
same readings itself, so the wording and the ceilings cannot drift apart.

Cost: two file reads (the Vault's list and one transcript tail per configured
agent), no model call, no network, nothing that starts the gateway.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nexgen_core.checks.context_budget_checks import check_context_budget
from nexgen_core.checks.turn_budget_checks import check_turn_budget
from nexgen_core.openclaw import flat_only
from nexgen_core.report import CheckOutcome


def outcomes(
    home_dir: Path, config: Mapping[str, Any], values: Mapping[str, str],
) -> list[CheckOutcome]:
    """The flat-model guard, then the seed and the turn readings, in that order.

    The guard comes first because it is the only one of the three that refuses
    something: a ref outside the declared flat list fails here, before any turn can
    spend on it. `openclaw.context_budget` judges the first prompt,
    `openclaw.turn_budget` judges the heaviest recent turn of every configured
    agent, because a session can pass at 78,717 tokens and still spend 74 calls of
    it (`docs/proposals/daru-burn.md`). A reading that measured nothing is left out
    rather than printed as a permanent "could not be verified": a machine that did
    not chat through a harness today has nothing wrong.
    """
    providers, notes = flat_only.load(home_dir)
    readings = [
        check_context_budget(home_dir, env=values),
        check_turn_budget(home_dir, config=config, env=values),
    ]
    return [
        flat_only.check(config, providers, notes),
        *(outcome for outcome in readings if outcome is not None),
    ]
