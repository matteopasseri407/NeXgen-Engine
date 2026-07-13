"""Regression test — "prune the vault" must route to `vault-groom`, not freehand grooming.

Before 2026-07-13, AGENTS.md's "Keep the garden" section inlined a full
heat-map-plus-semantic-judgement grooming methodology and told agents to
"run it" on "prune the vault" / "pota il vault" -- without ever mentioning
the `vault-groom` command at all. That meant any agent hearing that phrase
would freehand-groom the vault directly, bypassing every guardrail built
into vault-groom.sh/.ps1 the same day (the guarded propose/confirm/execute
flow, the exact-approved-text write pass, the mechanical completion check).

This pins that the fix stays fixed: the trigger phrases must be documented
next to an explicit instruction to invoke the real command.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AGENTS_MD = REPO / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"

GARDEN_MARKER = "Keep the garden"
COMMAND_MARKER = "invoke `vault-groom`"


def test_prune_the_vault_trigger_points_at_the_real_command():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert GARDEN_MARKER in text
    assert '"prune the vault"' in text
    assert '"pota il vault"' in text
    assert COMMAND_MARKER in text, (
        "AGENTS.md no longer tells agents to invoke the real vault-groom "
        "command on the prune-the-vault trigger -- it may have regressed "
        "back to describing freehand grooming methodology inline"
    )

    garden_idx = text.index(GARDEN_MARKER)
    command_idx = text.index(COMMAND_MARKER)
    assert command_idx > garden_idx
    between = text[garden_idx:command_idx]
    assert "\n# " not in between, "vault-groom routing drifted out of the Keep the garden section"


def test_relayed_confirmation_scoped_to_the_exact_shown_tranche():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "valid only for the EXACT tranche you showed them this turn" in text, (
        "AGENTS.md must still warn against reusing a prior/assumed approval "
        "when an agent relays vault-groom's confirmation on the user's behalf "
        "-- this is the exact mistake made live on 2026-07-13"
    )
