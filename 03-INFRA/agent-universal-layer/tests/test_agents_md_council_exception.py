"""Regression test — firecrawl-first hard rule vs. the Council's tool-block.

`AGENTS.md`'s "Search hard rule" says firecrawl MUST be used for every web
search/scrape, no exceptions. Separately, every Council seat prompt
(`council/prompts/*.md`) forbids the consulted model from using ANY tool,
firecrawl included, because seats run headless/sandboxed (see the
`--sandbox` / `--tools ""` / `-s read-only` flags and the "consulenti senza
mani" rationale in `council/council.py`). Both rules are intentional, but
read in isolation they contradict each other.

These checks pin that the contradiction stays reconciled: AGENTS.md must
document the Council as a named, deliberate exception living near the
firecrawl-first rule (not a silent omission), and the prompt files it
points to must still actually forbid tool use — if a future edit dropped
the tool-block line from a prompt, the documented exception would become
false and this test must catch that drift.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AGENTS_MD = REPO / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
PROMPTS_DIR = REPO / "03-INFRA" / "agent-universal-layer" / "council" / "prompts"

FIRECRAWL_MARKER = "Search hard rule — firecrawl FIRST"
COUNCIL_EXCEPTION_MARKER = "Council exception"
NO_TOOLS_PHRASE = "hai strumenti e non devi usarne"


def test_firecrawl_first_hard_rule_still_present():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert FIRECRAWL_MARKER in text, "firecrawl-first hard rule marker missing from AGENTS.md"
    assert "MANDATORY for every agent and every model, no exceptions" in text


def test_council_exception_documented_near_firecrawl_rule():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert COUNCIL_EXCEPTION_MARKER in text, (
        "AGENTS.md documents the firecrawl-first hard rule but not the Council's "
        "documented exception to it"
    )

    firecrawl_idx = text.index(FIRECRAWL_MARKER)
    exception_idx = text.index(COUNCIL_EXCEPTION_MARKER)
    assert exception_idx > firecrawl_idx, (
        "Council exception note must be documented after (near) the firecrawl-first rule"
    )

    # Same section: no other top-level heading between the rule and the
    # exception note that documents it.
    between = text[firecrawl_idx:exception_idx]
    assert "\n# " not in between, (
        "Council exception note drifted out of the section that holds the "
        "firecrawl-first rule"
    )

    exception_paragraph = text[exception_idx : exception_idx + 600]
    assert "council/prompts" in exception_paragraph
    assert "council.py" in exception_paragraph
    assert "headless" in exception_paragraph and "sandboxed" in exception_paragraph


def test_council_prompts_still_forbid_tool_use():
    """Guards the claim AGENTS.md makes about the prompts: every seat prompt
    must still actually block tool use, or the documented exception is lying."""
    prompt_files = sorted(PROMPTS_DIR.glob("*.md"))
    assert prompt_files, f"no Council prompt files found under {PROMPTS_DIR}"

    for path in prompt_files:
        text = path.read_text(encoding="utf-8")
        assert NO_TOOLS_PHRASE in text, (
            f"{path.name} no longer forbids tool use — AGENTS.md's Council exception "
            "note would then be documenting a rule that isn't true"
        )
