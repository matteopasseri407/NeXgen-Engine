"""Pinning dello split main/search di firecrawl prima del refactor.

Fissa il comportamento attuale (non quello desiderato): search su backend
finto ritorna esiti, main --help esce zero, firme pubbliche invariate.
Solo chiamate finte, mai rete, mai chiavi vere.
"""
from __future__ import annotations

import inspect
import io
import json
import sys
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import nexgen_core.tools.firecrawl as fc_mod  # noqa: E402


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("rete toccata")

    monkeypatch.setattr(socket, "create_connection", _blocked)


def _fake_urlopen_json(monkeypatch: pytest.MonkeyPatch, payload: object, seen: dict | None = None):
    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

    def _fake(request, timeout=None):
        if seen is not None:
            seen["url"] = request.full_url
            try:
                seen["body"] = json.loads(request.data.decode("utf-8")) if request.data else None
            except Exception:
                seen["body"] = None
            seen["auth"] = request.get_header("Authorization")
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake)


def _fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_URL", "http://finto.invalid")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "finta-chiave")


def test_search_su_backend_finto_ritorna_esito_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    _fake_env(monkeypatch)
    seen: dict = {}
    _fake_urlopen_json(
        monkeypatch,
        {"success": True, "data": {"web": [{"title": "T", "url": "https://example.com/"}]}},
        seen,
    )
    client = fc_mod.FirecrawlClient(api_url="http://finto.invalid", api_key="finta-chiave")
    res = client.search("gatto finto")
    assert res.get("success") is True
    assert seen["url"] == "http://finto.invalid/v2/search"
    assert seen["body"]["query"] == "gatto finto"
    assert seen["body"]["limit"] == 20
    assert seen["auth"] == "Bearer finta-chiave"


def test_search_su_backend_finto_ritorna_esiti_di_fallimento(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    _fake_env(monkeypatch)
    # Errore di provider finto: search riporta l'esito senza alzare.
    _fake_urlopen_json(monkeypatch, {"success": False, "error": "finto-giu"})
    client = fc_mod.FirecrawlClient(api_url="http://finto.invalid", api_key="finta-chiave")
    res = client.search("gatto finto")
    assert res.get("success") is False
    # Opzioni finte inoltrate al backend finto.
    seen: dict = {}
    _fake_urlopen_json(monkeypatch, {"success": True, "data": {"web": []}}, seen)
    res = client.search("gatto finto", limit=5, sources=["web"], scrape=True)
    assert res.get("success") is True
    assert seen["body"]["limit"] == 5
    assert seen["body"]["sources"] == ["web"]
    assert seen["body"]["scrapeOptions"] == {"formats": ["markdown"]}


def test_search_stabile_a_parita_di_backend_finto(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    _fake_env(monkeypatch)
    _fake_urlopen_json(monkeypatch, {"success": True, "data": {"web": []}})
    client = fc_mod.FirecrawlClient(api_url="http://finto.invalid", api_key="finta-chiave")
    first = client.search("gatto finto")
    second = client.search("gatto finto")
    assert first == second


def test_main_search_finto_ritorna_zero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    _no_network(monkeypatch)
    _fake_env(monkeypatch)

    def _fake_search(self, query, limit=20, sources=None, scrape=False, scrape_formats=None):
        assert query == "gatto finto"
        return {"success": True, "data": {"web": [{"title": "T", "url": "https://example.com/"}]}}

    monkeypatch.setattr(fc_mod.FirecrawlClient, "search", _fake_search)
    rc = fc_mod.main(["search", "gatto", "finto", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "success" in out


def test_main_help_esce_zero(capsys: pytest.CaptureFixture) -> None:
    # Senza argomenti: stampa l'aiuto e ritorna zero, senza uscire.
    rc = fc_mod.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "search" in out
    # --help globale: argparse esce con codice zero.
    with pytest.raises(SystemExit) as exc:
        fc_mod.main(["--help"])
    assert exc.value.code == 0


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(fc_mod.FirecrawlClient.search).parameters) == [
        "self",
        "query",
        "limit",
        "sources",
        "scrape",
        "scrape_formats",
    ]
    assert list(inspect.signature(fc_mod.FirecrawlClient.scrape).parameters) == [
        "self",
        "url",
        "formats",
    ]
    assert list(inspect.signature(fc_mod.FirecrawlClient.check_status).parameters) == ["self"]
    assert list(inspect.signature(fc_mod.main).parameters) == ["argv"]
