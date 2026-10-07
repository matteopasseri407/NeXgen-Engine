"""The Vault's copy of the leak patterns is compared with the engine's, not trusted to memory."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks.security_checks import check_leak_patterns_twin  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402


def _layout(tmp_path, source_text, twin_text=None):
    engine = tmp_path / "engine"
    source = engine / "agent-universal-layer" / "leak-scan" / "leak_patterns.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(source_text, encoding="utf-8")
    vault = tmp_path / "vault"
    if twin_text is not None:
        twin = vault / "03-INFRA" / "agent-universal-layer" / "sanitize" / "leak_patterns.yaml"
        twin.parent.mkdir(parents=True)
        twin.write_text(twin_text, encoding="utf-8")
    return vault, engine


def test_identical_copies_are_ok_whatever_the_trailing_whitespace(tmp_path):
    vault, engine = _layout(tmp_path, "leak_hard:\n  - 'a'\n", "leak_hard:  \n  - 'a'\n\n")
    assert check_leak_patterns_twin(vault, engine).severity == Severity.OK


def test_a_copy_missing_a_pattern_is_flagged_with_the_fix(tmp_path):
    vault, engine = _layout(tmp_path, "leak_hard:\n  - 'a'\n  - 'b'\n", "leak_hard:\n  - 'a'\n")
    outcome = check_leak_patterns_twin(vault, engine)
    assert outcome.severity == Severity.WARN
    assert "leak_patterns.yaml" in outcome.action


def test_a_machine_without_a_vault_copy_has_nothing_to_compare(tmp_path):
    vault, engine = _layout(tmp_path, "leak_hard: []\n")
    assert check_leak_patterns_twin(vault, engine) is None
