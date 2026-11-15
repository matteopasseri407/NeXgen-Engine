"""`context_tokens: N`: the per-call context cap written on the model's own entry.

Expected values are the literals `docs/proposals/daru-burn.md` L2 names (200,000 as
the recommended cap) and the shape `docs.openclaw.ai` documents for
`models.providers.<provider>.models[].contextTokens`.
"""
from __future__ import annotations

import pytest
from nexgen_core import i18n
from nexgen_core.openclaw import context_cap, plan
from nexgen_core.openclaw.squad import apply_squad

CLAUDE_REF = "anthropic/claude-opus-5-5"


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: the sentence asserted below is the English one."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def test_a_declared_cap_lands_on_the_model_entry():
    config: dict = {}
    squad = {"agents": {"capo": {"model": CLAUDE_REF, "context_tokens": 200000}}}

    context_cap.apply(config, squad)

    assert config["models"]["providers"]["anthropic"]["models"] == [
        {"id": "claude-opus-5-5", "contextTokens": 200000},
    ]


def test_the_native_window_is_never_invented():
    config: dict = {}
    squad = {"agents": {"capo": {"model": CLAUDE_REF, "context_tokens": 200000}}}

    context_cap.apply(config, squad)

    entry = config["models"]["providers"]["anthropic"]["models"][0]
    assert "contextWindow" not in entry


def test_an_agent_without_the_key_writes_nothing():
    config: dict = {}
    squad = {"agents": {"capo": {"model": CLAUDE_REF}}}

    context_cap.apply(config, squad)

    assert "models" not in config


def test_an_existing_model_entry_keeps_every_field_it_carried():
    config = {"models": {"providers": {"anthropic": {"models": [
        {"id": "claude-opus-5-5", "contextWindow": 1000000, "input": ["text", "image"]},
    ]}}}}
    squad = {"agents": {"capo": {"model": CLAUDE_REF, "context_tokens": 200000}}}

    context_cap.apply(config, squad)

    assert config["models"]["providers"]["anthropic"]["models"] == [
        {"id": "claude-opus-5-5", "contextWindow": 1000000, "input": ["text", "image"],
         "contextTokens": 200000},
    ]


def test_a_provider_block_that_did_not_exist_keeps_its_other_models():
    config = {"models": {"providers": {"anthropic": {"models": [
        {"id": "claude-sonnet-5-5", "contextWindow": 200000},
    ]}}}}
    squad = {"agents": {"capo": {"model": CLAUDE_REF, "context_tokens": 200000}}}

    context_cap.apply(config, squad)

    models = config["models"]["providers"]["anthropic"]["models"]
    assert [entry["id"] for entry in models] == ["claude-sonnet-5-5", "claude-opus-5-5"]


def test_the_smallest_cap_wins_when_two_agents_share_one_model():
    config: dict = {}
    squad = {"agents": {
        "capo": {"model": CLAUDE_REF, "context_tokens": 200000},
        "triage": {"model": CLAUDE_REF, "context_tokens": 120000},
    }}

    context_cap.apply(config, squad)

    assert context_cap.caps(squad) == {("anthropic", "claude-opus-5-5"): 120000}
    assert config["models"]["providers"]["anthropic"]["models"][0]["contextTokens"] == 120000


def test_a_cap_that_is_not_a_positive_whole_number_is_ignored():
    for value in (True, "200000", 0, -5, 200000.5, None):
        assert context_cap.declared_cap({"context_tokens": value}) is None


def test_the_proposal_does_not_call_a_capped_model_an_unknown_window():
    """The note is about a detected window the backend did not report, not about a cap."""
    config = {"models": {"providers": {"anthropic": {"models": [
        {"id": "claude-opus-5-5", "contextTokens": 200000},
    ]}}}}

    assert plan._context_notes(config) == []


def test_an_uncapped_model_with_no_window_is_still_named():
    config = {"models": {"providers": {"openai": {"models": [{"id": "gpt-6-luna"}]}}}}

    assert plan._context_notes(config) == [
        "The context window is unknown for these models and is not written: gpt-6-luna",
    ]


def test_the_squad_writes_the_cap_on_the_model_it_names():
    config = {"agents": {"entries": {}}, "tools": {"byProvider": {}}}
    squad = {"agents": {"capo": {"name": "capo", "model": CLAUDE_REF, "context_tokens": 200000}}}

    applied = apply_squad(config, squad)

    assert applied["models"]["providers"]["anthropic"]["models"] == [
        {"id": "claude-opus-5-5", "contextTokens": 200000},
    ]
