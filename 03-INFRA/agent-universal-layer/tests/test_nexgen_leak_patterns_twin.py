"""The Vault's copy of the leak rules is compared with the engine's, not trusted to memory."""
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
    source = engine / "agent-universal-layer" / "leak-scan" / "gitleaks.toml"
    source.parent.mkdir(parents=True)
    source.write_text(source_text, encoding="utf-8")
    vault = tmp_path / "vault"
    if twin_text is not None:
        twin = vault / "03-INFRA" / "agent-universal-layer" / "sanitize" / "gitleaks.toml"
        twin.parent.mkdir(parents=True)
        twin.write_text(twin_text, encoding="utf-8")
    return vault, engine


def test_identical_copies_are_ok_whatever_the_trailing_whitespace(tmp_path):
    vault, engine = _layout(tmp_path, "title = 'a'\n", "title = 'a'  \n\n")
    assert check_leak_patterns_twin(vault, engine).severity == Severity.OK


def test_a_copy_missing_a_rule_is_flagged_with_the_fix(tmp_path):
    vault, engine = _layout(tmp_path, "rules = ['a', 'b']\n", "rules = ['a']\n")
    outcome = check_leak_patterns_twin(vault, engine)
    assert outcome.severity == Severity.WARN
    assert "gitleaks.toml" in outcome.action


def test_a_machine_without_a_vault_copy_has_nothing_to_compare(tmp_path):
    vault, engine = _layout(tmp_path, "rules = []\n")
    assert check_leak_patterns_twin(vault, engine) is None


def test_the_engine_side_is_the_rule_file_the_gate_actually_reads(tmp_path):
    """A rename of the rule file must not quietly turn this check into a no-op."""
    engine = tmp_path / "engine"
    source = engine / "agent-universal-layer" / "leak-scan" / "gitleaks.toml"
    source.parent.mkdir(parents=True)
    source.write_text("rules = []\n", encoding="utf-8")
    twin = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer" / "sanitize" / "gitleaks.toml"
    twin.parent.mkdir(parents=True)
    twin.write_text("rules = ['x']\n", encoding="utf-8")

    assert check_leak_patterns_twin(tmp_path / "vault", engine).severity == Severity.WARN
