"""Regression test — native per-CLI memory is never authoritative.

The 2026-07-13 follow-up review found AGENTS.md said nothing about each
host CLI's OWN cross-session memory (e.g. Claude Code's own memory file) --
only that generated derivatives (CLAUDE.md, MCP config) are read-only copies
of the Vault. That left native memory free to silently accumulate its own
parallel, divergent understanding of the user/project alongside the Vault,
with nothing telling an agent that channel is not authoritative, or telling
a Council seat not to answer from it instead of the turn's own context.

These checks pin that both rules stay present and land near the sections
they govern (Knowledge Vault; the Council tool-block rules) -- not a
one-time addition that could silently drift or get edited out later.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AGENTS_MD = REPO / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"

KNOWLEDGE_VAULT_MARKER = "# Knowledge Vault"
NATIVE_MEMORY_MARKER = "Native memory is never authoritative"
COUNCIL_ENFORCEMENT_MARKER = "Council CLI-level enforcement is asymmetric"
COUNCIL_SEAT_MEMORY_MARKER = "Council seats answer from the turn's context only"


def test_native_memory_rule_present_in_knowledge_vault_section():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert KNOWLEDGE_VAULT_MARKER in text
    assert NATIVE_MEMORY_MARKER in text, (
        "AGENTS.md no longer tells agents their own native/built-in memory "
        "is non-authoritative relative to the Vault"
    )

    vault_idx = text.index(KNOWLEDGE_VAULT_MARKER)
    native_idx = text.index(NATIVE_MEMORY_MARKER)
    assert native_idx > vault_idx, "native-memory rule drifted out of the Knowledge Vault section"

    between = text[vault_idx:native_idx]
    assert "\n# " not in between, "native-memory rule drifted into a different top-level section"

    paragraph = text[native_idx : native_idx + 700]
    assert "vault-library" in paragraph
    assert "offline-emergency-mode.md" in paragraph


def test_council_seats_instructed_not_to_use_native_memory():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert COUNCIL_ENFORCEMENT_MARKER in text
    assert COUNCIL_SEAT_MEMORY_MARKER in text, (
        "AGENTS.md no longer tells Council seats to answer from the turn's "
        "context instead of their own native cross-session memory"
    )

    enforcement_idx = text.index(COUNCIL_ENFORCEMENT_MARKER)
    seat_idx = text.index(COUNCIL_SEAT_MEMORY_MARKER)
    assert seat_idx > enforcement_idx, (
        "Council seat memory rule must be documented near the Council tool-block rules"
    )
    between = text[enforcement_idx:seat_idx]
    assert "\n# " not in between, "Council seat memory rule drifted out of the Council section"
