"""Pinning dello split required-rules prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con firme e
bootstrap finti: missing_signatures su firme finte stabile, main del
tool coerente con missing, check_required_rules coerente col tool,
firme pubbliche invariate. Solo file finti in tmp_path, mai file veri.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks.security_checks import check_required_rules  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402
from nexgen_core.tools.required_rules import (  # noqa: E402
    DEFAULT_RULES,
    load_signatures,
    main,
    missing_signatures,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_vault(vault: Path, agents_text: str, rules_text: str) -> tuple[Path, Path]:
    instr = vault / "03-INFRA" / "agent-universal-layer" / "instructions"
    agents = _write(instr / "AGENTS.md", agents_text)
    rules = _write(instr / "required-rules.txt", rules_text)
    return agents, rules


def test_missing_signatures_su_firme_finte_stabile(tmp_path: Path) -> None:
    rules = _write(
        tmp_path / "rules.txt",
        "# commento\n\nFAKE-SIG-ONE\n  FAKE-SIG-TWO  \n# altro commento\n",
    )
    agents = _write(tmp_path / "AGENTS.md", "contiene solo FAKE-SIG-ONE qui\n")
    first = missing_signatures(agents, rules)
    second = missing_signatures(agents, rules)
    assert first == ["FAKE-SIG-TWO"]
    assert second == first
    full = _write(tmp_path / "full.md", "FAKE-SIG-ONE e FAKE-SIG-TWO presenti\n")
    assert missing_signatures(full, rules) == []


def test_load_signatures_ignora_commenti_e_vuote(tmp_path: Path) -> None:
    rules = _write(tmp_path / "rules.txt", "# solo commenti\n\n#  e vuote\n")
    assert load_signatures(rules) == []
    rules2 = _write(tmp_path / "rules2.txt", "# h\nSIG-A\n\nSIG-B\n")
    assert load_signatures(rules2) == ["SIG-A", "SIG-B"]


def test_main_coerente_con_missing(tmp_path: Path) -> None:
    rules = _write(tmp_path / "rules.txt", "FAKE-SIG-ONE\nFAKE-SIG-TWO\n")
    partial = _write(tmp_path / "partial.md", "solo FAKE-SIG-ONE\n")
    assert main([str(partial), str(rules)]) == 1
    full = _write(tmp_path / "full.md", "FAKE-SIG-ONE\nFAKE-SIG-TWO\n")
    assert main([str(full), str(rules)]) == 0
    assert main([str(tmp_path / "assente.md"), str(rules)]) == 2
    assert main([str(full), str(tmp_path / "no-rules.txt")]) == 2


def test_check_coerente_col_tool(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    agents, rules = _make_vault(
        vault,
        "contiene solo FAKE-SIG-ONE qui\n",
        "FAKE-SIG-ONE\nFAKE-SIG-TWO\n",
    )
    expected_missing = missing_signatures(agents, rules)
    assert expected_missing == ["FAKE-SIG-TWO"]
    outcome = check_required_rules(vault)
    assert outcome.id == "bootstrap.rules_guard"
    assert outcome.severity == Severity.BROKEN
    assert (outcome.detail or "").split(", ") == expected_missing

    _make_vault(vault, "FAKE-SIG-ONE\nFAKE-SIG-TWO\n", "FAKE-SIG-ONE\nFAKE-SIG-TWO\n")
    ok = check_required_rules(vault)
    assert ok.id == "bootstrap.rules_guard"
    assert ok.severity == Severity.OK


def test_check_canon_mancante_resta_undetermined(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _make_vault(vault, "FAKE-SIG-ONE\n", "FAKE-SIG-ONE\n")
    (vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md").unlink()
    outcome = check_required_rules(vault)
    assert outcome.id == "bootstrap.rules_guard"
    assert outcome.severity == Severity.UNDETERMINED


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(load_signatures).parameters) == ["path"]
    assert list(inspect.signature(missing_signatures).parameters) == ["target", "rules"]
    assert list(inspect.signature(main).parameters) == ["argv"]
    assert list(inspect.signature(check_required_rules).parameters) == ["vault_data", "engine_root"]
    assert DEFAULT_RULES.name == "required-rules.txt"
