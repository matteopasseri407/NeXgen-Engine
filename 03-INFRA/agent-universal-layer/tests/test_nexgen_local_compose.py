"""Test del cancello mail: bozza del modello, busta del motore, invio con --yes.

Nessun test tocca un account reale: il connettore e' sempre finto.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexgen_local.compose import (
    MailError,
    apply_mail,
    format_gate,
    list_proposals,
    propose_mail,
)
from nexgen_local.config import LaneConfig
from nexgen_local.connectors import ConnectorError


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


class DraftLLM:
    def __init__(self, body: str = "Certo, allego tutto entro lunedi'.") -> None:
        self.body = body
        self.seen: list[str] = []

    def json(self, system: str, user: str) -> dict | None:
        return None

    def text(self, system: str, user: str) -> str:
        self.seen.append(user)
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
        return {"id": "sent1", "to": _ORIGINAL["from"], "subject": "Re: Budget Falco Rosso"}

    def _send(to: str, subject: str, body: str, cc: str = "", http=None) -> dict:
        sent["send"] = (to, subject, body)
        return {"id": "sent2"}

    monkeypatch.setattr(gmail_conn, "reply_to", _reply)
    monkeypatch.setattr(gmail_conn, "send_message", _send)


def test_propose_reply_builds_envelope_from_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    _fake_gmail(monkeypatch, {})
    llm = DraftLLM()
    proposal = propose_mail(llm, cfg, "conferma l'invio entro lunedi'", reply_to="m1")
    assert proposal.kind == "reply"
    assert proposal.to == "commercialista@esempio.it"
    assert proposal.subject == "Re: Budget Falco Rosso"
    assert proposal.in_reply_to == "m1"
    assert "lunedi" in proposal.body
    assert "Mandami le fatture" in llm.seen[0]  # il modello vede il contesto, non decide la busta
    saved = cfg.mails_dir / f"{proposal.id}.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["to"] == proposal.to


def test_propose_send_needs_a_named_address(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(MailError, match="destinatario"):
        propose_mail(DraftLLM(), cfg, "saluta", to="non-un-indirizzo")
    proposal = propose_mail(DraftLLM(), cfg, "saluta", to="Scrivi a mario@esempio.it", subject="Ciao")
    assert proposal.kind == "send"
    assert proposal.to == "mario@esempio.it"


def test_propose_reply_refuses_unreadable_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nexgen_local.connectors.gmail as gmail_conn

    cfg = _cfg(tmp_path)

    def _boom(mid: str, http=None):
        raise ConnectorError("(posta non configurata: serve il login una tantum)")

    monkeypatch.setattr(gmail_conn, "get_message", _boom)
    with pytest.raises(MailError, match="originale non leggibile"):
        propose_mail(DraftLLM(), cfg, "rispondi", reply_to="m1")


def test_apply_sends_once_and_refuses_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    sent: dict = {}
    _fake_gmail(monkeypatch, sent)
    proposal = propose_mail(DraftLLM(), cfg, "conferma", reply_to="m1")
    with pytest.raises(MailError, match="--yes"):
        apply_mail(cfg, proposal.id, yes=False)
    assert sent == {}
    result = apply_mail(cfg, proposal.id, yes=True)
    assert result == {"id": proposal.id, "sent": True, "sent_id": "sent1", "to": "commercialista@esempio.it"}
    assert sent["reply"][0] == "m1"
    with pytest.raises(MailError, match="gia' inviata"):
        apply_mail(cfg, proposal.id, yes=True)
    lines = cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3  # proposta + intento + esito


def test_apply_without_account_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.connectors.gmail as gmail_conn

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(gmail_conn, "get_message", lambda mid, http=None: dict(_ORIGINAL))
    proposal = propose_mail(DraftLLM(), cfg, "conferma", reply_to="m1")

    def _nologin(*args, **kwargs):
        raise ConnectorError("(posta non configurata: serve il login una tantum)")

    monkeypatch.setattr(gmail_conn, "reply_to", _nologin)
    with pytest.raises(MailError, match="invio fallito"):
        apply_mail(cfg, proposal.id, yes=True)
    assert load_applied(cfg, proposal.id) == ""


def load_applied(cfg: LaneConfig, proposal_id: str) -> str:
    from nexgen_local.compose import load_proposal

    return load_proposal(cfg, proposal_id).applied_at


def test_mail_propose_cli_passes_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    import nexgen_local.connectors.outlook as outlook_conn
    from nexgen_local import cli as cli_module

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        outlook_conn, "get_message",
        lambda mid, http=None: {"id": mid, "from": "a@example.com", "to": "", "subject": "S",
                                "date": "", "snippet": "", "body": "B", "attachments": []},
    )
    monkeypatch.setattr(cli_module, "_config", lambda args: cfg)
    monkeypatch.setattr(cli_module, "_llm", lambda cfg: DraftLLM("Bozza."))
    args = argparse.Namespace(instruction="ok", reply="o9", to="", subject="", provider="outlook",
                              model=None, json=True)
    assert cli_module.cmd_mail_propose(args) == 0


def test_gate_shows_machine_facts_and_full_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    _fake_gmail(monkeypatch, {})
    proposal = propose_mail(DraftLLM("Corpo breve."), cfg, "istruzione", reply_to="m1")
    gate = format_gate(proposal)
    assert "commercialista@esempio.it" in gate
    assert "Re: Budget Falco Rosso" in gate
    assert "Corpo breve." in gate
    assert f"mail-send {proposal.id} --yes" in gate
    assert [p.id for p in list_proposals(cfg)] == [proposal.id]


def test_sent_mail_claims_are_flagged_without_gate_receipts() -> None:
    from nexgen_local.engine import verify_answer

    read = [{"tool": "read_mail", "args": {"id": "m1"}, "ok": True}]
    assert verify_answer("Ho inviato la mail al commercialista.", read, "contenuto")
    assert verify_answer("Ho mandato tutto ieri.", read, "contenuto")


def test_propose_mail_outlook_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same gate, Outlook backend: envelope from Graph, artifact tagged."""
    import nexgen_local.connectors.outlook as outlook_conn

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        outlook_conn, "get_message",
        lambda mid, http=None: {
            "id": mid, "from": "capo@esempio.it", "to": "", "subject": "Riunione",
            "date": "ieri", "snippet": "", "body": "Ci vediamo?", "attachments": [],
        },
    )
    sent: dict = {}
    monkeypatch.setattr(
        outlook_conn, "reply_to", lambda mid, body, http=None: sent.update(mid=mid) or {"id": "s9"}
    )
    llm = DraftLLM("Confermo.")
    proposal = propose_mail(llm, cfg, "conferma", reply_to="o9", provider="outlook")
    assert proposal.provider == "outlook"
    assert proposal.to == "capo@esempio.it"
    assert "outlook" in format_gate(proposal)
    result = apply_mail(cfg, proposal.id, yes=True)
    assert result["sent_id"] == "s9"
    assert sent["mid"] == "o9"
    with pytest.raises(MailError, match="non supportato"):
        propose_mail(llm, cfg, "x", reply_to="o9", provider="fax")


def test_old_proposals_load_as_gmail(tmp_path: Path) -> None:
    """Artifacts written before providers existed default to gmail."""
    import json as _json

    cfg = _cfg(tmp_path)
    cfg.mails_dir.mkdir(parents=True, exist_ok=True)
    old = {
        "id": "20260101-000000-ab12cd34", "kind": "send", "to": "a@example.com",
        "subject": "s", "in_reply_to": "", "body": "b", "instruction": "i",
    }
    (cfg.mails_dir / f"{old['id']}.json").write_text(_json.dumps(old), encoding="utf-8")
    from nexgen_local.compose import load_proposal

    assert load_proposal(cfg, old["id"]).provider == "gmail"


def test_same_body_twice_gets_unique_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same original, same second: two drafts, two artifacts, no overwrite."""
    import nexgen_local.connectors.gmail as gmail_conn

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(gmail_conn, "get_message", lambda mid, http=None: dict(_ORIGINAL))
    first = propose_mail(DraftLLM("Confermo."), cfg, "conferma", reply_to="m1")
    second = propose_mail(DraftLLM("Confermo."), cfg, "conferma", reply_to="m1")
    assert first.id != second.id
    assert {proposal.id for proposal in list_proposals(cfg)} == {first.id, second.id}


def test_mail_propose_never_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A colliding id refuses instead of replacing the shown draft."""
    import nexgen_local.compose as compose_module
    import nexgen_local.connectors.gmail as gmail_conn

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(gmail_conn, "get_message", lambda mid, http=None: dict(_ORIGINAL))
    monkeypatch.setattr(compose_module, "new_proposal_id", lambda: "20260101-000000-deadbeef")
    first = propose_mail(DraftLLM("Confermo."), cfg, "conferma", reply_to="m1")
    assert first.id == "20260101-000000-deadbeef"
    with pytest.raises(MailError, match="collisione"):
        propose_mail(DraftLLM("Altro testo."), cfg, "conferma", reply_to="m1")
    from nexgen_local.compose import load_proposal

    assert load_proposal(cfg, first.id).body == "Confermo."
