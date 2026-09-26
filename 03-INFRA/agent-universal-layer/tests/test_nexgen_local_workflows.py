"""Test del cancello workflow: allowlist nominativa, propose, run con --yes.

L'HTTP e' sempre finto. L'allowlist vive in un file temporaneo.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.workflows import (
    HttpResult,
    WorkflowError,
    confirm_run,
    format_gate,
    list_proposals,
    load_proposal,
    propose_run,
)


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir()
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        workflows_dir=tmp_path / "workflows",
    )


def _allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "allowlist.json"
    path.write_text(
        json.dumps(
            {"workflows": {"telegram-send": {
                "webhook_url": "https://n8n.example/webhook/tg",
                "description": "Invia un file su Telegram",
            }}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEXGEN_WORKFLOWS_ALLOWLIST", str(path))


def test_propose_and_run_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    _allowlist(tmp_path, monkeypatch)
    proposal = propose_run(cfg, "telegram-send", {"file": "a.txt"})
    assert "telegram-send" in format_gate(proposal)
    assert "n8n.example" not in format_gate(proposal)  # l'URL non si stampa mai
    assert [p.id for p in list_proposals(cfg)] == [proposal.id]
    with pytest.raises(WorkflowError, match="confirm"):
        confirm_run(cfg, proposal.id, False)

    calls: list = []

    def _fake(url: str, payload: bytes, headers: dict, timeout: int) -> HttpResult:
        calls.append((url, json.loads(payload.decode()), headers))
        return HttpResult(200, b'{"ok": true}')

    done = confirm_run(cfg, proposal.id, True, http=_fake)
    assert done["ran"] is True and done["outcome"] == '{"ok": true}'
    assert calls[0][0] == "https://n8n.example/webhook/tg"
    assert calls[0][1] == {"file": "a.txt"}
    with pytest.raises(WorkflowError, match="gia' eseguita"):
        confirm_run(cfg, proposal.id, True, http=_fake)
    assert len(calls) == 1


def test_unknown_workflow_and_bad_params_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    _allowlist(tmp_path, monkeypatch)
    with pytest.raises(WorkflowError, match="non consentito"):
        propose_run(cfg, " distruggi-tutto ", {})
    with pytest.raises(WorkflowError, match="oggetto JSON"):
        propose_run(cfg, "telegram-send", ["lista"])  # type: ignore[arg-type]
    with pytest.raises(WorkflowError, match="non valido"):
        load_proposal(cfg, "../evil")


def test_missing_allowlist_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("NEXGEN_WORKFLOWS_ALLOWLIST", str(tmp_path / "assente.json"))
    with pytest.raises(WorkflowError, match="allowlist"):
        propose_run(cfg, "telegram-send", {})


def test_failed_webhook_is_a_refused_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    _allowlist(tmp_path, monkeypatch)
    proposal = propose_run(cfg, "telegram-send", {})

    def _boom(url: str, payload: bytes, headers: dict, timeout: int) -> HttpResult:
        return HttpResult(500, b"errore")

    with pytest.raises(WorkflowError, match="HTTP 500"):
        confirm_run(cfg, proposal.id, True, http=_boom)


def test_wf_cli_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local import cli as cli_module

    cfg = _cfg(tmp_path)
    _allowlist(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_module, "_config", lambda args: cfg)
    args = argparse.Namespace(workflow="telegram-send", params='{"file": "a.txt"}', json=False)
    assert cli_module.cmd_wf_propose(args) == 0
    proposal_id = next(iter(cfg.workflows_dir.glob("*.json"))).stem
    args = argparse.Namespace(proposal_id=proposal_id, yes=False, json=False)
    assert cli_module.cmd_wf_run(args) == 1
    import nexgen_local.workflows as wf_module

    monkeypatch.setattr(
        wf_module, "_default_http", lambda url, payload, headers, timeout: HttpResult(200, b"ok")
    )
    args = argparse.Namespace(proposal_id=proposal_id, yes=True, json=True)
    assert cli_module.cmd_wf_run(args) == 0
