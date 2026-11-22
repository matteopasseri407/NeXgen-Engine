# nexgen: one-job reason="tests for the flat-only guard inside the OpenClaw doctor's spend block"
"""What `nexgen openclaw doctor` says about models that run on a pay-per-token provider.

The spend block already reads what the recent turns cost. This is the other half of the
same bill: a model ref in the file is a bill the next turn has not made yet, and a metered
one is refused here, before it runs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_core import i18n
from nexgen_core.openclaw import doctor_spend
from nexgen_core.report import Severity


@pytest.fixture(autouse=True)
def _english_notes():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def quota_file(home: Path, text: str) -> Path:
    """The `quota.yaml` the engine reads, at the path it reads it from."""
    path = home / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def flat(outcomes):
    """The one line this file is about."""
    return [outcome for outcome in outcomes if outcome.id == "openclaw.flat_models"]


def test_a_metered_ref_is_a_failure(tmp_path: Path) -> None:
    """FAIL, not WARN: a run on this ref spends money nobody approved."""
    quota_file(tmp_path, "flat_providers:\n  - opencode-go\n")
    config = {"agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5"}}}}

    line = flat(doctor_spend.outcomes(tmp_path, config, {}))

    assert len(line) == 1
    assert line[0].severity == Severity.BROKEN
    assert "anthropic/claude-opus-5-5" in line[0].message


def test_a_flat_configuration_passes_without_a_transcript(tmp_path: Path) -> None:
    """A machine that did not chat today has nothing to measure, and its refs are still judged."""
    quota_file(tmp_path, "flat_providers:\n  - opencode-go\n")
    config = {"agents": {"entries": {"main": {"model": "opencode-go/deepseek-v4.1-flash"}}}}

    outcomes = doctor_spend.outcomes(tmp_path, config, {})

    assert [(outcome.id, outcome.severity) for outcome in outcomes] == [("openclaw.flat_models", Severity.OK)]


def test_a_machine_that_declared_nothing_is_not_failed(tmp_path: Path) -> None:
    """The key is the owner's switch: without it the engine keeps the behaviour it had."""
    config = {"agents": {"entries": {"main": {"model": "anthropic/claude-opus-5-5"}}}}

    outcomes = doctor_spend.outcomes(tmp_path, config, {})

    assert all(outcome.severity != Severity.BROKEN for outcome in outcomes)


def test_a_declaration_that_cannot_be_used_is_a_warning(tmp_path: Path) -> None:
    quota_file(tmp_path, "flat_providers: opencode-go\n")
    config = {"agents": {"entries": {"main": {"model": "anthropic/claude-opus-5-5"}}}}

    line = flat(doctor_spend.outcomes(tmp_path, config, {}))

    assert line[0].severity == Severity.WARN
