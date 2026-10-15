"""Pinning dello split propose/approve-target/apply di patch prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con repo finti:
propose costruisce proposta con hash, apply senza --yes rifiuta,
apply con --yes su repo finto applica. Solo tmp_path, solo git locale
del repo finto, nessuna rete, nessuna HOME reale, nessuna scrittura
fuori tmp.
"""
from __future__ import annotations

import hashlib
import inspect
import subprocess
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.patch import PatchError, apply_proposal, propose_patch


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


class _FakeLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def json(self, system: str, user: str) -> dict:
        return self.payload

    def text(self, system: str, user: str) -> str:
        return ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def test_propose_builds_proposal_with_hash(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_bytes("Questo contiente un refuso.\n".encode("utf-8"))
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(
        _FakeLLM({"old": "contiente", "new": "contiene", "why": "refuso"}),
        cfg,
        "notes.md",
        "correggi il refuso",
    )
    assert proposal.original_hash == _sha("Questo contiente un refuso.\n")
    assert proposal.new_hash == _sha("Questo contiene un refuso.\n")
    assert proposal.patch.strip()
    assert proposal.dry_run is True
    assert "-Questo contiente un refuso." in proposal.patch
    assert "+Questo contiene un refuso." in proposal.patch
    # La proposta non scrive sul file: solo artefatto.
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiente un refuso.\n"


def test_apply_without_yes_refuses(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_bytes("Questo contiente un refuso.\n".encode("utf-8"))
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(
        _FakeLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix"
    )
    with pytest.raises(PatchError):
        apply_proposal(cfg, proposal.id, yes=False)
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiente un refuso.\n"


def test_apply_with_yes_applies_on_fake_repo(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_bytes("Questo contiente un refuso.\n".encode("utf-8"))
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(
        _FakeLLM({"old": "contiente", "new": "contiene", "why": "refuso"}),
        cfg,
        "notes.md",
        "correggi il refuso",
    )
    result = apply_proposal(cfg, proposal.id, yes=True)
    assert result["applied"] is True
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiene un refuso.\n"


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(propose_patch).parameters) == [
        "llm",
        "cfg",
        "file_rel",
        "instruction",
    ]
    assert list(inspect.signature(apply_proposal).parameters) == [
        "cfg",
        "proposal_id",
        "yes",
        "verify",
    ]


def test_split_helpers_in_sequence() -> None:
    import nexgen_local.patch as patch_mod

    src = Path(patch_mod.__file__).read_text(encoding="utf-8")
    assert "def _build_proposal" in src
    assert "def _check_approval" in src
    assert "def _run_apply" in src
    propose_body = src[src.index("def propose_patch") :]
    propose_body = propose_body[: propose_body.index("def apply_proposal")]
    assert "_build_proposal(" in propose_body
    apply_body = src[src.index("def _apply_proposal") :]
    assert apply_body.index("_check_approval(") < apply_body.index("_run_apply(")
