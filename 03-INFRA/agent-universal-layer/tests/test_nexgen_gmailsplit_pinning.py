"""Pinning dello split gmail: fissa il comportamento attuale, non quello desiderato.

Lettura con backend finto ritorna i campi attesi, invio passa dal gate di
conferma del provider, errore del backend finto solleva un errore chiaro con
una sola chiamata (nessun retry). Solo HTTP finto, solo tmp_path, nessuna
rete, nessun token vero (token sintetici locali).
"""
from __future__ import annotations

import base64
import inspect
import json
import time
from pathlib import Path

import pytest

from nexgen_local.connectors import ConnectorError
from nexgen_local.connectors import gmail as gmail_mod
from nexgen_local.connectors.auth import HttpResult


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


def _gmail_full() -> bytes:
    body = base64.urlsafe_b64encode("Il budget e' 900 euro.".encode()).decode()
    return json.dumps(
        {
            "id": "m1",
            "threadId": "t1",
            "snippet": "Il budget e' 900 euro.",
            "payload": {
                "headers": [
                    {"name": "From", "value": "commercialista@esempio.it"},
                    {"name": "To", "value": "io@esempio.it"},
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


def _http(routes: dict[str, tuple[int, bytes]], calls: list[str]):
    def call(req, timeout: int) -> HttpResult:
        calls.append(req.full_url)
        url = req.full_url
        for marker, (status, body) in routes.items():
            if marker in url:
                return HttpResult(status, body)
        raise AssertionError(f"chiamata non prevista: {url}")

    return call


def test_read_message_returns_expected_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token_dir(tmp_path, monkeypatch)
    calls: list[str] = []
    http = _http({"/messages/m1?": (200, _gmail_full())}, calls)
    msg = gmail_mod.get_message("m1", http=http)
    assert set(msg) == {
        "id", "threadId", "from", "to", "subject", "date",
        "message-id", "snippet", "body", "coverage", "attachments",
    }
    assert msg["id"] == "m1"
    assert msg["threadId"] == "t1"
    assert msg["from"] == "commercialista@esempio.it"
    assert msg["to"] == "io@esempio.it"
    assert msg["subject"] == "Budget Falco Rosso"
    assert "900 euro" in msg["body"]
    assert msg["coverage"] == "text"
    assert msg["attachments"] == ["allegato.pdf (application/pdf)"]
    assert len(calls) == 1  # una lettura, una chiamata


def test_send_passes_provider_confirmation_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token_dir(tmp_path, monkeypatch)
    captured: dict = {}

    def ok(req, timeout: int) -> HttpResult:
        captured["url"] = req.full_url
        captured["body"] = req.data
        return HttpResult(200, b'{"id": "sent9"}')

    sent = gmail_mod.send_message("mario@esempio.it", "Ciao", "Corpo.", http=ok)
    assert sent == {"id": "sent9"}
    raw = base64.urlsafe_b64decode(json.loads(captured["body"].decode())["raw"]).decode()
    assert "To: mario@esempio.it" in raw and "Subject: Ciao" in raw and "Corpo." in raw

    calls: list[str] = []
    denied = _http({"/messages/send": (200, b"{}")}, calls)
    with pytest.raises(ConnectorError, match="non confermato"):
        gmail_mod.send_message("mario@esempio.it", "Ciao", "Corpo.", http=denied)
    assert len(calls) == 1  # il gate interroga il provider una volta sola


def test_backend_error_raises_clear_error_without_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token_dir(tmp_path, monkeypatch)
    read_calls: list[str] = []
    with pytest.raises(ConnectorError, match="ricerca fallita"):
        gmail_mod.search_messages(
            "from:commercialista", http=_http({"/messages?q=": (500, b"{}")}, read_calls)
        )
    assert len(read_calls) == 1

    send_calls: list[str] = []
    with pytest.raises(ConnectorError, match="invio fallito"):
        gmail_mod.send_message(
            "mario@esempio.it", "Ciao", "Corpo.",
            http=_http({"/messages/send": (500, b"{}")}, send_calls),
        )
    assert len(send_calls) == 1

    down_calls: list[str] = []

    def _down(req, timeout: int) -> HttpResult:
        down_calls.append(req.full_url)
        raise OSError("finto down")

    with pytest.raises(ConnectorError, match="non raggiungibile"):
        gmail_mod.get_message("m1", http=_down)
    assert len(down_calls) == 1


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(gmail_mod.search_messages).parameters) == [
        "query", "max_results", "http",
    ]
    assert list(inspect.signature(gmail_mod.get_message).parameters) == ["mid", "http"]
    assert list(inspect.signature(gmail_mod.send_message).parameters) == [
        "to", "subject", "body", "cc", "http",
    ]
    assert list(inspect.signature(gmail_mod.reply_to).parameters) == [
        "message_id", "body", "http",
    ]


def test_read_send_sections_share_common_helper() -> None:
    src = Path(gmail_mod.__file__).read_text(encoding="utf-8")
    assert "def _call_backend" in src
    assert "READ" in src and "SEND" in src
    assert src.count("_call_backend(") >= 2  # lettura e invio passano dall'helper
    assert src.index("READ") < src.index("def get_message")
    assert src.index("def get_message") < src.index("SEND")
    assert src.index("SEND") < src.index("def send_message")
