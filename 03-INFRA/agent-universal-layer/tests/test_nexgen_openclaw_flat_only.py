# nexgen: one-job reason="tests for the flat provider list, the refs it covers and the doctor line"
"""Models that must stay on a flat plan: the list, the places a ref spends, and the line.

The Vault here is a `quota.yaml` under `tmp_path`, never this machine's: a test whose
answer changes with whose subscription the suite runs beside is not a test. The list is
the owner's rule, so an absent key must change nothing, and a typo must cost one note,
not the command.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_core import i18n
from nexgen_core.openclaw import flat_only
from nexgen_core.report import Severity

#: Two providers on a flat plan, written the way the owner would write them.
QUOTA = "flat_providers:\n  - opencode-go\n  - google-antigravity-cli\n"


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: the sentences are asserted below."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def quota_file(home: Path, text: str = QUOTA) -> Path:
    """The `quota.yaml` the engine reads, at the path it reads it from."""
    path = home / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_no_declared_list_reads_as_no_restriction(tmp_path: Path) -> None:
    """A machine that never wrote the key keeps every provider: the guard changes nothing."""
    providers, notes = flat_only.load(tmp_path)

    assert (providers, notes) == ((), [])
    outcome = flat_only.check({"agents": {"entries": {"main": {"model": "google/gemini-3-pro"}}}}, providers)
    assert outcome.id == flat_only.CHECK_ID
    assert outcome.severity == Severity.OK
    assert "flat_providers" in outcome.message


def test_the_declared_providers_are_read_from_the_quota_file(tmp_path: Path) -> None:
    quota_file(tmp_path, "flat_providers:\n  - opencode-go\n  - '  google-antigravity-cli  '\n  - opencode-go\n")

    assert flat_only.declared(tmp_path) == ("opencode-go", "google-antigravity-cli")


def test_a_file_that_cannot_be_read_costs_the_guard_not_the_command(tmp_path: Path) -> None:
    quota_file(tmp_path).write_text("flat_providers: [\n", encoding="utf-8")

    providers, notes = flat_only.load(tmp_path)

    assert providers == ()
    assert notes and "quota.yaml" in notes[0]


def test_a_key_that_is_not_a_list_is_named_and_ignored(tmp_path: Path) -> None:
    quota_file(tmp_path, "flat_providers: opencode-go\n")

    providers, notes = flat_only.load(tmp_path)

    assert providers == ()
    assert notes and "flat_providers" in notes[0]


def test_an_entry_that_is_not_a_provider_id_is_named(tmp_path: Path) -> None:
    """A ref written where a provider belongs is a typo that must not pass silently."""
    quota_file(tmp_path, "flat_providers:\n  - opencode-go\n  - opencode-go/deepseek-v4.1-flash\n")

    providers, notes = flat_only.load(tmp_path)

    assert providers == ("opencode-go",)
    assert any("opencode-go/deepseek-v4.1-flash" in note for note in notes)


def test_a_metered_agent_model_is_a_failure() -> None:
    config = {"agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5"}}}}

    outcome = flat_only.check(config, ("opencode-go", "google-antigravity-cli"))

    assert outcome.severity == Severity.BROKEN
    assert "capo" in outcome.message
    assert "anthropic/claude-opus-5-5" in outcome.message
    assert outcome.action, "a failure a person cannot act on is a failure they learn to skip"


def test_a_metered_default_chain_is_a_failure() -> None:
    config = {"agents": {"defaults": {"model": {
        "primary": "opencode-go/deepseek-v4.1-flash",
        "fallbacks": ["google/gemini-3-pro"],
    }}}}

    outcome = flat_only.check(config, ("opencode-go",))

    assert outcome.severity == Severity.BROKEN
    assert "google/gemini-3-pro" in outcome.message


def test_a_metered_shared_subagent_model_is_a_failure() -> None:
    config = {"agents": {"defaults": {"subagents": {"model": "anthropic/claude-haiku-4-5"}}}}

    outcome = flat_only.check(config, ("opencode-go",))

    assert outcome.severity == Severity.BROKEN
    assert "anthropic/claude-haiku-4-5" in outcome.message


def test_a_metered_per_agent_subagent_model_is_a_failure() -> None:
    config = {"agents": {"entries": {"capo": {
        "model": "opencode-go/deepseek-v4.1-flash",
        "subagents": {"model": "google/gemini-3-pro"},
    }}}}

    outcome = flat_only.check(config, ("opencode-go",))

    assert outcome.severity == Severity.BROKEN
    assert "google/gemini-3-pro" in outcome.message


def test_a_flat_configuration_passes() -> None:
    config = {
        "agents": {
            "defaults": {
                "model": "opencode-go/deepseek-v4.1-flash",
                "subagents": {"model": "opencode-go/deepseek-v4.1-flash"},
            },
            "entries": {"capo": {"model": {
                "primary": "google-antigravity-cli/gemini-3.8-flash",
                "fallbacks": ["opencode-go/deepseek-v4.1-pro"],
            }}},
        },
    }

    outcome = flat_only.check(config, ("opencode-go", "google-antigravity-cli"))

    assert outcome.severity == Severity.OK
    assert "4" in outcome.message, "the line says how many refs were judged"


def test_a_declared_list_that_cannot_be_used_is_a_warning() -> None:
    """The guard is off because the list is unreadable: that is work, not a broken machine."""
    outcome = flat_only.check({"agents": {}}, (), ["'flat_providers' is not a list of provider ids: ignored."])

    assert outcome.severity == Severity.WARN
    assert "flat_providers" in outcome.message


def test_a_ref_that_names_no_provider_is_left_to_the_checks_that_judge_it() -> None:
    """An alias is resolved elsewhere; this check judges providers and says nothing about it."""
    outcome = flat_only.check({"agents": {"entries": {"main": {"model": "sonnet"}}}}, ("opencode-go",))

    assert outcome.severity == Severity.OK
