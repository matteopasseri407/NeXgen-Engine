"""Pinning dello split draft/send di compose prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con backend finti:
propose_mail costruisce la bozza senza inviare, apply senza --yes rifiuta,
apply con --yes su backend finto invia. Solo tmp_path, solo backend finti,
nessuna rete, nessun invio vero.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from nexgen_local.compose import MailError, apply_mail, load_proposal, propose_mail
from nexgen_local.config import LaneConfig


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir()
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        mails_dir=tmp_path / "mails",
    )


class _DraftLLM:
    def __init__(self, body: str = "Certo, confermo entro lunedi'.") -> None:
        self.body = body

    def json(self, system: str, user: str) -> dict | None:
        return None

    def text(self, system: str, user: str) -> str:
        return self.body


_ORIGINAL = {
    "id": "m1",
    "threadId": "t1",
    "from": "commercialista@esempio.it",
    "to": "io@esempio.it",
    "subject": "Budget Falco Rosso",
    "date": "ieri",
    "message-id": "<abc123@mail>",
    "snippet": "",
    "body": "Mandami le fatture.",
    "attachments": [],
}


def _fake_gmail(monkeypatch: pytest.MonkeyPatch, sent: dict) -> None:
    import nexgen_local.connectors.gmail as gmail_conn

    monkeypatch.setattr(gmail_conn, "get_message", lambda mid, http=None: dict(_ORIGINAL))

    def _reply(message_id: str, body: str, http=None) -> dict:
        sent["reply"] = (message_id, body)
        return {"id": "sent1"}

    def _send(to: str, subject: str, body: str, cc: str = "", http=None) -> dict:
        sent["send"] = (to, subject, body)
        return {"id": "sent2"}

    monkeypatch.setattr(gmail_conn, "reply_to", _reply)
    monkeypatch.setattr(gmail_conn, "send_message", _send)


def test_propose_mail_builds_draft_without_sending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    sent: dict = {}
    _fake_gmail(monkeypatch, sent)
    proposal = propose_mail(_DraftLLM(), cfg, "conferma l'invio entro lunedi'", reply_to="m1")
    assert proposal.kind == "reply"
    assert proposal.to == "commercialista@esempio.it"
    assert proposal.subject == "Re: Budget Falco Rosso"
    assert proposal.in_reply_to == "m1"
    assert "lunedi" in proposal.body
    assert proposal.content_sha
    assert sent == {}  # la bozza non invia per costruzione
    saved = json.loads((cfg.mails_dir / f"{proposal.id}.json").read_text(encoding="utf-8"))
    assert saved["to"] == proposal.to
    assert saved["body"] == proposal.body


def test_apply_without_yes_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    sent: dict = {}
    _fake_gmail(monkeypatch, sent)
    proposal = propose_mail(_DraftLLM(), cfg, "conferma", reply_to="m1")
    with pytest.raises(MailError, match="--yes"):
        apply_mail(cfg, proposal.id, yes=False)
    assert sent == {}  # nessun invio senza approvazione esplicita
    assert load_proposal(cfg, proposal.id).applied_at == ""


def test_apply_with_yes_sends_on_fake_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    sent: dict = {}
    _fake_gmail(monkeypatch, sent)
    proposal = propose_mail(_DraftLLM(), cfg, "conferma", reply_to="m1")
    result = apply_mail(cfg, proposal.id, yes=True)
    assert result == {
        "id": proposal.id,
        "sent": True,
        "sent_id": "sent1",
        "to": "commercialista@esempio.it",
    }
    assert sent["reply"][0] == "m1"
    assert "lunedi" in sent["reply"][1]
    assert load_proposal(cfg, proposal.id).applied_at != ""


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(propose_mail).parameters) == [
        "llm",
        "cfg",
        "instruction",
        "reply_to",
        "to",
        "subject",
        "provider",
    ]
    assert list(inspect.signature(apply_mail).parameters) == ["cfg", "proposal_id", "yes"]


def test_draft_send_helpers_in_sequence() -> None:
    import nexgen_local.compose as compose_mod

    src = Path(compose_mod.__file__).read_text(encoding="utf-8")
    assert "def _build_draft" in src
    assert "def _send_mail" in src
    propose_body = src[src.index("def propose_mail_from_context") : src.index("def mail_envelope")]
    assert "_build_draft(" in propose_body
    apply_body = src[src.index("def _apply_mail") :]
    assert "_send_mail(" in apply_body
    assert apply_body.index("refuse_prior_attempt(") < apply_body.index("_send_mail(")
    assert apply_body.index("validate_content(") < apply_body.index("_send_mail(")
    assert apply_body.index("record_attempt(") < apply_body.index("_send_mail(")
