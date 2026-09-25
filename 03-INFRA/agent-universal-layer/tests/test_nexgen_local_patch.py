"""Test della penna: proposta, cancello a fatti macchina, applicazione, staleness.

Il modello non scrive mai: propone uno snippet, il motore valida, calcola il
diff e fa il dry-run. L'applicazione e' un comando separato che riverifica
l'hash del file prima di toccarlo.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.patch import PatchError, apply_proposal, format_gate, load_proposal, propose_patch


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def _cfg(tmp_path: Path, repo: Path) -> LaneConfig:
    return LaneConfig(
        vault_root=tmp_path / "vault",
        repo_roots=(repo,),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        proposals_dir=tmp_path / "proposals",
    )


class PatchLLM:
    """Fake: risponde sempre lo stesso JSON, come un router prescritto."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def json(self, system: str, user: str) -> dict:
        return self.payload

    def text(self, system: str, user: str) -> str:
        return ""


def test_propose_and_apply_happy_path(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    llm = PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"})
    proposal = propose_patch(llm, cfg, "notes.md", "correggi il refuso")
    assert proposal.dry_run is True
    assert "-Questo contiente un refuso." in proposal.patch
    assert "+Questo contiene un refuso." in proposal.patch
    result = apply_proposal(cfg, proposal.id, yes=True)
    assert result["applied"] is True
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiene un refuso.\n"
    assert len(cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_apply_requires_explicit_yes(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")
    with pytest.raises(PatchError):
        apply_proposal(cfg, proposal.id, yes=False)
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiente un refuso.\n"


def test_stale_proposal_is_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")
    (repo / "notes.md").write_text("Il file e' cambiato nel frattempo.\n", encoding="utf-8")
    with pytest.raises(PatchError, match="stantia"):
        apply_proposal(cfg, proposal.id, yes=True)


def test_ambiguous_snippet_is_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("alfa e ancora alfa.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    with pytest.raises(PatchError, match="esattamente una volta"):
        propose_patch(PatchLLM({"old": "alfa", "new": "beta"}), cfg, "notes.md", "cambia")


def test_file_outside_repo_is_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "nota.md").write_text("contenuto\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    with pytest.raises(PatchError, match="fuori dai root"):
        propose_patch(PatchLLM({"old": "contenuto", "new": "altro"}), cfg, "nota.md", "cambia")
    with pytest.raises(PatchError, match="fuori dai root"):
        propose_patch(PatchLLM({"old": "x", "new": "y"}), cfg, "../fuori.md", "cambia")


def test_model_prose_is_never_evidence(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    liar = PatchLLM({"old": "contiente", "new": "contiene", "why": "ho gia' applicato e verificato tutto io"})
    proposal = propose_patch(liar, cfg, "notes.md", "fix")
    gate = format_gate(proposal)
    assert "non verificato" in gate
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiente un refuso.\n"
    apply_proposal(cfg, proposal.id, yes=True)
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiene un refuso.\n"


def test_proposal_id_traversal_is_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    cfg = _cfg(tmp_path, repo)
    with pytest.raises(PatchError, match="non valido"):
        load_proposal(cfg, "../evil")
    with pytest.raises(PatchError, match="non valido"):
        load_proposal(cfg, "20260925-213501-ab12cd34/../../x")


def test_failed_dry_run_blocks_application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.patch as patch_module

    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    monkeypatch.setattr(patch_module, "_dry_run", lambda root, text: (False, "dry-run simulato fallito"))
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")
    assert proposal.dry_run is False
    with pytest.raises(PatchError, match="dry-run"):
        apply_proposal(cfg, proposal.id, yes=True)
