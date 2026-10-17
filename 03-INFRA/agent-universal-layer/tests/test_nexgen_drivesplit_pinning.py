"""Pinning dello split stage/confirm/upload di drive prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con backend finto:
lettura file ritorna il contenuto atteso, stage prepara senza inviare,
confirm senza stage rifiuta, upload passa dal gate di conferma del provider.
Solo HTTP finto, solo tmp_path, nessuna rete, nessun token vero (token
sintetici locali).
"""
from __future__ import annotations

import inspect
import json
import time
from pathlib import Path

import pytest

from nexgen_local.connectors import ConnectorError
from nexgen_local.connectors import drive as drive_mod
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


def _http(routes: dict[str, tuple[int, bytes]], calls: list[str]):
    def call(req, timeout: int) -> HttpResult:
        calls.append(req.full_url)
        url = req.full_url
        for marker, (status, body) in routes.items():
            if marker in url:
                return HttpResult(status, body)
        raise AssertionError(f"chiamata non prevista: {url}")

    return call


def _unreachable(req, timeout: int):
    raise AssertionError(f"rete toccata: {req.full_url}")


def test_read_file_returns_expected_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token_dir(tmp_path, monkeypatch)
    meta = {"id": "d3", "name": "note.txt", "mimeType": "text/plain", "modifiedTime": ""}
    calls: list[str] = []
    http = _http({"alt=media": (200, "contenuto testuale".encode("utf-8"))}, calls)
    doc = drive_mod.read_file("d3", meta=meta, http=http)
    assert doc["id"] == "d3"
    assert doc["name"] == "note.txt"
    assert doc["text"] == "contenuto testuale"
    assert len(calls) == 1  # una lettura, una chiamata


def test_stage_prepares_without_sending() -> None:
    # Nessun token, nessun http: lo stage non puo' inviare per costruzione.
    staged = drive_mod._stage_upload(b"ciao", "a.txt", "text/plain")
    assert isinstance(staged, dict)
    assert staged["name"] == "a.txt"
    assert b"ciao" in staged["body"]
    assert b'"name": "a.txt"' in staged["body"]


def test_confirm_without_stage_refuses() -> None:
    with pytest.raises(ConnectorError, match="stage"):
        drive_mod._confirm_upload(None)
    with pytest.raises(ConnectorError, match="stage"):
        drive_mod._confirm_upload({})
    # Upload senza stage rifiuta prima di ogni rete.
    calls: list[str] = []
    with pytest.raises(ConnectorError, match="stage"):
        drive_mod._upload_file(None, http=_http({}, calls))
    assert calls == []


def test_upload_passes_confirmation_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token_dir(tmp_path, monkeypatch)
    staged = drive_mod._stage_upload(b"ciao", "a.txt", "text/plain")
    confirmed = drive_mod._confirm_upload(staged)
    assert confirmed["name"] == "a.txt"
    # Gate del provider: risposta senza id rifiutata, una sola chiamata.
    calls: list[str] = []
    denied = _http({"/upload/drive/v3/files": (200, b"{}")}, calls)
    with pytest.raises(ConnectorError, match="non confermato"):
        drive_mod._upload_file(confirmed, http=denied)
    assert len(calls) == 1
    # La API pubblica attraversa lo stesso gate.
    pub_calls: list[str] = []
    denied_pub = _http({"/upload/drive/v3/files": (200, b"{}")}, pub_calls)
    with pytest.raises(ConnectorError, match="non confermato"):
        drive_mod.upload_file(b"ciao", "a.txt", "text/plain", http=denied_pub)
    assert len(pub_calls) == 1
    # Happy path: id del provider confermato.
    ok_calls: list[str] = []
    ok = _http(
        {"/upload/drive/v3/files": (200, b'{"id": "d7", "name": "a.txt"}')}, ok_calls
    )
    out = drive_mod.upload_file(b"ciao", "a.txt", "text/plain", http=ok)
    assert out == {"id": "d7", "name": "a.txt"}
    assert len(ok_calls) == 1


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(drive_mod.search_files).parameters) == [
        "query", "max_results", "http",
    ]
    assert list(inspect.signature(drive_mod.read_file).parameters) == [
        "file_id", "meta", "http",
    ]
    assert list(inspect.signature(drive_mod.download_bytes).parameters) == [
        "file_id", "http",
    ]
    assert list(inspect.signature(drive_mod.upload_file).parameters) == [
        "data", "name", "mime_type", "folder_id", "http",
    ]


def test_stage_confirm_upload_helpers_in_sequence() -> None:
    src = Path(drive_mod.__file__).read_text(encoding="utf-8")
    assert "def _stage_upload" in src
    assert "def _confirm_upload" in src
    assert "def _upload_file" in src
    body = src[src.index("def upload_file"):]
    assert body.index("_stage_upload(") < body.index("_confirm_upload(")
    assert body.index("_confirm_upload(") < body.index("_upload_file(")
