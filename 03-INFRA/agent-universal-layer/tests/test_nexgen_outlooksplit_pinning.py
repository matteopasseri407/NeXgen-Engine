"""Pinning dello split AUTH/READ/SEND di outlook prima del refactor.

Solo chiamate finte, mai rete, mai token veri: i token vivono in una dir
temporanea e l'HTTP e' sempre iniettato. Fissa tre comportamenti attuali che
il refactor (sezioni AUTH/READ/SEND + helper comune `_call_backend`) non deve
cambiare: lettura con i campi attesi, invio col gate di conferma, auth
duplicata verso `token_store`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexgen_local.connectors import ConnectorError, NeedsLogin
from nexgen_local.connectors import token_store as store_mod


def _outlook_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "ol-tokens"
    directory.mkdir()
    (directory / "tokens.json").write_text(
        json.dumps({"refresh_token": "r", "access_token": "a", "expires_at": 9999999999}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OUTLOOK_TOKEN_DIR", str(directory))
    monkeypatch.setenv("OUTLOOK_CLIENT_ID", "test-client")
    return directory


def _unreachable(req, timeout: int):
    raise AssertionError(f"rete toccata: {req.full_url}")


def test_read_message_returns_expected_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.connectors import outlook as outlook_mod

    _outlook_env(tmp_path, monkeypatch)
    payload = {
        "id": "o9",
        "subject": "Riunione",
        "from": {"emailAddress": {"address": "capo@esempio.it"}},
        "toRecipients": [
            {"emailAddress": {"address": "io@esempio.it"}},
            {"emailAddress": {"address": "tu@esempio.it"}},
        ],
        "receivedDateTime": "2026-09-22T09:00:00Z",
        "bodyPreview": "Riunione giovedi",
        "body": {"contentType": "html", "content": "<p>Riunione <b>giovedi</b></p>"},
        "hasAttachments": True,
    }

    def call(req, timeout: int):
        assert "/me/messages/o9?" in req.full_url
        return store_mod.HttpResult(200, json.dumps(payload).encode())

    msg = outlook_mod.get_message("o9", http=call)
    assert msg["id"] == "o9"
    assert msg["from"] == "capo@esempio.it"
    assert msg["to"] == "io@esempio.it, tu@esempio.it"
    assert msg["subject"] == "Riunione"
    assert msg["date"] == "2026-09-22T09:00:00Z"
    assert msg["snippet"] == "Riunione giovedi"
    assert msg["body"] == "Riunione giovedi"
    assert msg["coverage"] == "html"
    assert msg["attachments"] == ["(allegati presenti)"]


def test_send_passes_confirmation_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.connectors import outlook as outlook_mod

    _outlook_env(tmp_path, monkeypatch)
    # Gate di conferma: destinatario invalido e corpo vuoto rifiutati prima di ogni rete.
    with pytest.raises(ConnectorError, match="destinatario"):
        outlook_mod.send_message("senza-chiocciola", "Oggetto", "Corpo.", http=_unreachable)
    with pytest.raises(ConnectorError, match="corpo vuoto"):
        outlook_mod.send_message("io@esempio.it", "Oggetto", "   ", http=_unreachable)
    # Conferma del provider: Graph sendMail risponde 202 vuoto, l'id resta "inviato".
    captured: dict = {}

    def call(req, timeout: int):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return store_mod.HttpResult(202, b"")

    assert outlook_mod.send_message("io@esempio.it", "Ciao", "Corpo.", http=call) == {"id": "inviato"}
    assert captured["url"].endswith("/me/sendMail")
    assert captured["payload"]["message"]["toRecipients"] == [
        {"emailAddress": {"address": "io@esempio.it"}}
    ]
    # Il provider che rifiuta resta un invio fallito, non un id inventato.
    def fail(req, timeout: int):
        return store_mod.HttpResult(400, b"{}")

    with pytest.raises(ConnectorError, match="invio fallito"):
        outlook_mod.send_message("io@esempio.it", "Ciao", "Corpo.", http=fail)


def test_duplicate_auth_towards_token_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.connectors import outlook as outlook_mod

    directory = _outlook_env(tmp_path, monkeypatch)
    # L'auth duplicata resta una delega a token_store: stessi tipi, stessa dir, niente rete.
    assert outlook_mod.HttpResult is store_mod.HttpResult
    assert outlook_mod.HttpFn is store_mod.HttpFn
    assert outlook_mod.token_dir() == directory
    assert outlook_mod.token_file() == directory / "tokens.json"
    ok, _detail = outlook_mod.status()
    assert ok is True
    assert outlook_mod.access_token(http=_unreachable) == "a"
    # Senza client id niente rete: serve la registrazione app + login una tantum.
    monkeypatch.setenv("OUTLOOK_TOKEN_DIR", str(tmp_path / "vuota"))
    monkeypatch.delenv("OUTLOOK_CLIENT_ID", raising=False)
    with pytest.raises(NeedsLogin, match="registrazione|login"):
        outlook_mod.search_messages("x", http=_unreachable)
