"""Test dei connettori personali (Gmail, Drive): forme, errori, niente rete.

L'HTTP e' sempre finto; i token vivono in una dir temporanea. Nessun test
tocca un account reale: il login resta un passo umano, mai di suite.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from nexgen_local.connectors import ConnectorError, NeedsLogin
from nexgen_local.connectors.auth import HttpResult, access_token
from nexgen_local.connectors import drive as drive_mod
from nexgen_local.connectors import gmail as gmail_mod


def _token_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "tokens"
    directory.mkdir()
    (directory / "tokens.json").write_text(
        json.dumps(
            {"refresh_token": "r", "access_token": "a", "expires_at": int(time.time()) + 3600}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKSPACE_MCP_TOKEN_DIR", str(directory))
    return directory


def _http(routes: dict[str, tuple[int, bytes]]):
    def call(req, timeout: int) -> HttpResult:
        url = req.full_url
        for marker, (status, body) in routes.items():
            if marker in url:
                return HttpResult(status, body)
        raise AssertionError(f"chiamata non prevista: {url}")

    return call


def _gmail_full() -> bytes:
    body = base64.urlsafe_b64encode("Il budget e' 900 euro.".encode()).decode()
    return json.dumps(
        {
            "id": "m1",
            "snippet": "Il budget e' 900 euro.",
            "payload": {
                "headers": [
                    {"name": "From", "value": "commercialista@esempio.it"},
                    {"name": "Subject", "value": "Budget Falco Rosso"},
                    {"name": "Date", "value": "Mon, 21 Sep 2026 10:00:00 +0200"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "filename": "",
                     "body": {"data": body}},
                    {"mimeType": "application/pdf", "filename": "allegato.pdf",
                     "body": {"attachmentId": "x"}},
                ],
            },
        }
    ).encode()


def test_gmail_search_returns_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    http = _http({"/messages?q=": (200, b'{"messages": [{"id": "m1", "threadId": "t1"}]}')})
    hits = gmail_mod.search_messages("from:commercialista", http=http)
    assert hits == [{"id": "m1", "threadId": "t1"}]


def test_gmail_search_empty_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    http = _http({"/messages?q=": (200, b"{}")})
    assert gmail_mod.search_messages("zzzinesistente", http=http) == []


def test_gmail_get_decodes_body_and_lists_attachments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token_dir(tmp_path, monkeypatch)
    http = _http({"/messages/m1?": (200, _gmail_full())})
    msg = gmail_mod.get_message("m1", http=http)
    assert msg["from"] == "commercialista@esempio.it"
    assert msg["subject"] == "Budget Falco Rosso"
    assert "900 euro" in msg["body"]
    assert msg["attachments"] == ["allegato.pdf (application/pdf)"]


def test_gmail_401_is_expired_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    http = _http({"/messages": (401, b"{}")})
    with pytest.raises(ConnectorError, match="accesso scaduto"):
        gmail_mod.search_messages("x", http=http)


def test_no_tokens_means_needs_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKSPACE_MCP_TOKEN_DIR", str(tmp_path / "vuota"))
    with pytest.raises(NeedsLogin, match="login una tantum"):
        access_token()
    with pytest.raises(NeedsLogin, match="login una tantum"):
        gmail_mod.search_messages("x")


def test_drive_search_returns_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    payload = {"files": [{"id": "d1", "name": "Contratto.txt", "mimeType": "text/plain",
                          "modifiedTime": "2026-09-01T00:00:00Z"}]}
    http = _http({"/drive/v3/files?": (200, json.dumps(payload).encode())})
    hits = drive_mod.search_files("contratto", http=http)
    assert hits[0]["name"] == "Contratto.txt"


def test_drive_read_exports_google_doc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    meta = {"id": "d2", "name": "Contratto", "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-09-01T00:00:00Z"}
    http = _http({"/export?": (200, "Clausola 3: pagamento a 30 giorni.".encode())})
    doc = drive_mod.read_file("d2", meta=meta, http=http)
    assert "Clausola 3" in doc["text"]


def test_drive_read_downloads_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    meta = {"id": "d3", "name": "note.txt", "mimeType": "text/plain", "modifiedTime": ""}
    http = _http({"alt=media": (200, "contenuto testuale".encode())})
    doc = drive_mod.read_file("d3", meta=meta, http=http)
    assert doc["text"] == "contenuto testuale"


def test_drive_pdf_is_refused_as_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    meta = {"id": "d4", "name": "scan.pdf", "mimeType": "application/pdf", "modifiedTime": ""}
    with pytest.raises(ConnectorError, match="non leggibile come testo"):
        drive_mod.read_file("d4", meta=meta, http=lambda req, t: HttpResult(200, b""))


def _post_fake(captured: dict, response: bytes = b'{"id": "sent9"}'):
    def call(req, timeout: int) -> HttpResult:
        captured["url"] = req.full_url
        captured["body"] = req.data
        return HttpResult(200, response)

    return call


def test_send_message_builds_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    captured: dict = {}
    sent = gmail_mod.send_message("mario@esempio.it", "Ciao", "Corpo.", http=_post_fake(captured))
    assert sent == {"id": "sent9"}
    import base64
    import json as _json

    posted = _json.loads(captured["body"].decode())
    raw = base64.urlsafe_b64decode(posted["raw"]).decode()
    assert "To: mario@esempio.it" in raw and "Subject: Ciao" in raw and "Corpo." in raw


def test_reply_to_threads_from_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    captured: dict = {}
    import json as _json

    payload = _json.loads(_gmail_full())
    payload["threadId"] = "t1"
    payload["payload"]["headers"].append({"name": "Message-ID", "value": "<abc@mail>"})

    def call(req, timeout: int) -> HttpResult:
        if req.full_url.endswith("/messages/send"):
            captured["post"] = _json.loads(req.data.decode())
            return HttpResult(200, b'{"id": "sent8"}')
        return HttpResult(200, _json.dumps(payload).encode())

    sent = gmail_mod.reply_to("m1", "Rispondo.", http=call)
    assert sent["to"] == "commercialista@esempio.it"
    assert sent["subject"] == "Re: Budget Falco Rosso"
    assert captured["post"].get("threadId") == "t1"
    import base64

    raw = base64.urlsafe_b64decode(captured["post"]["raw"]).decode()
    assert "In-Reply-To: <abc@mail>" in raw


def test_send_without_provider_confirmation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token_dir(tmp_path, monkeypatch)
    http = _http({"/messages/send": (200, b"{}")})
    with pytest.raises(ConnectorError, match="non confermato"):
        gmail_mod.send_message("mario@esempio.it", "Ciao", "Corpo.", http=http)


def _outlook_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "ol-tokens"
    directory.mkdir()
    (directory / "tokens.json").write_text(
        json.dumps(
            {"refresh_token": "r", "access_token": "a", "expires_at": 9999999999}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OUTLOOK_TOKEN_DIR", str(directory))
    monkeypatch.setenv("OUTLOOK_CLIENT_ID", "test-client")
    return directory


def test_outlook_search_returns_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.connectors import outlook as outlook_mod

    _outlook_env(tmp_path, monkeypatch)
    http = _http({"/me/messages?": (200, b'{"value": [{"id": "o9"}]}')})
    assert outlook_mod.search_messages("budget", http=http) == [{"id": "o9"}]


def test_outlook_get_strips_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.connectors import outlook as outlook_mod

    _outlook_env(tmp_path, monkeypatch)
    payload = {
        "id": "o9",
        "subject": "Riunione",
        "from": {"emailAddress": {"address": "capo@esempio.it"}},
        "toRecipients": [{"emailAddress": {"address": "io@esempio.it"}}],
        "receivedDateTime": "2026-09-22T09:00:00Z",
        "bodyPreview": "Riunione giovedi",
        "body": {"contentType": "html", "content": "<p>Riunione <b>giovedi</b></p>"},
        "hasAttachments": False,
    }
    http = _http({"/me/messages/o9?": (200, json.dumps(payload).encode())})
    msg = outlook_mod.get_message("o9", http=http)
    assert msg["from"] == "capo@esempio.it"
    assert msg["body"] == "Riunione giovedi"
    assert msg["attachments"] == []


def test_outlook_without_client_id_needs_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexgen_local.connectors import NeedsLogin
    from nexgen_local.connectors import outlook as outlook_mod

    monkeypatch.setenv("OUTLOOK_TOKEN_DIR", str(tmp_path / "vuota"))
    monkeypatch.delenv("OUTLOOK_CLIENT_ID", raising=False)
    with pytest.raises(NeedsLogin, match="registrazione|login"):
        outlook_mod.search_messages("x")


def test_outlook_reply_threads_server_side(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.connectors import outlook as outlook_mod

    _outlook_env(tmp_path, monkeypatch)
    payload = {
        "id": "o9",
        "subject": "Riunione",
        "from": {"emailAddress": {"address": "capo@esempio.it"}},
        "toRecipients": [],
        "receivedDateTime": "2026-09-22T09:00:00Z",
        "bodyPreview": "",
        "body": {"contentType": "text", "content": "ciao"},
        "hasAttachments": False,
    }
    captured: dict = {}

    def call(req, timeout: int) -> HttpResult:
        if req.full_url.endswith("/reply"):
            captured["comment"] = json.loads(req.data.decode())["comment"]
            return HttpResult(202, b"")
        return HttpResult(200, json.dumps(payload).encode())

    sent = outlook_mod.reply_to("o9", "Confermo.", http=call)
    assert sent["to"] == "capo@esempio.it"
    assert captured["comment"] == "Confermo."
