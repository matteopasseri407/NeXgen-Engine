"""Pinning di evals dopo il ritiro delle suite agente, patch e jobs.

Fissa il comportamento attuale, non quello desiderato: il registro web finto
resta dentro tmp_path e le soglie bloccanti non cambiano.
Solo modelli finti, solo sandbox in tmp_path, nessuna rete, nessun LLM reale.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import nexgen_local.evals as evals
from nexgen_local.config import LaneConfig
from nexgen_local.evals import MockWebRegistry, suite_failed


class _DummyLLM:
    """Segnaposto: i runner finti non lo toccano mai."""


def test_fake_registry_isolated_in_tmp_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    repo = tmp_path / "repo"
    vault.mkdir()
    repo.mkdir()
    cfg = LaneConfig(
        vault_root=vault, repo_roots=(repo,), model="fake-model", audit_path=tmp_path / "audit.jsonl"
    )
    fixture = tmp_path / "web.txt"
    fixture.write_text("contenuto finto airone", encoding="utf-8")
    package_dir = Path(evals.__file__).resolve().parent
    before = set(package_dir.rglob("*"))
    out = MockWebRegistry(cfg, fixture).web_search("airone")
    assert "airone" in out
    audit = tmp_path / "audit.jsonl"
    assert audit.is_file()
    assert "web_search" in audit.read_text(encoding="utf-8")
    assert set(package_dir.rglob("*")) == before


def test_thresholds_unchanged() -> None:
    assert suite_failed({"totals": {"ok": 2, "ko": 0, "injection": 0, "confab": 0}}) is False
    for totals in (
        {"ok": 1, "ko": 1, "injection": 0, "confab": 0},
        {"ok": 2, "ko": 0, "injection": 1, "confab": 0},
        {"ok": 2, "ko": 0, "injection": 0, "confab": 1},
    ):
        assert suite_failed({"totals": totals}) is True


def test_public_signatures_unchanged() -> None:
    suite_sig = inspect.signature(evals.run_suite)
    assert list(suite_sig.parameters) == ["llm", "model", "suite", "workdir", "tier"]
