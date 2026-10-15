"""Pinning dell'unify probe di firecrawl prima del refactor.

Fissa il comportamento attuale (non quello desiderato): probe su backend
finto ritorna l'esito atteso, probe stabile a parita' di backend finto,
firme pubbliche invariate. Solo chiamate finte, mai rete, mai chiavi vere.
"""
from __future__ import annotations

import inspect
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import nexgen_core.tools.firecrawl_health as health_mod  # noqa: E402


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("rete toccata")

    monkeypatch.setattr(socket, "create_connection", _blocked)


def _fake_urlopen_json(monkeypatch: pytest.MonkeyPatch, payload: object, seen: dict | None = None):
    class _FakeResp(io.StringIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fake(request, timeout=None):
        if seen is not None:
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            try:
                seen["body"] = json.loads(request.data.decode("utf-8"))
            except Exception:
                seen["body"] = None
            seen["auth"] = request.get_header("Authorization")
        return _FakeResp(json.dumps(payload))

    monkeypatch.setattr(urllib.request, "urlopen", _fake)


def _fake_urlopen_error(monkeypatch: pytest.MonkeyPatch, exc: BaseException):
    def _fake(request, timeout=None):
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", _fake)


def test_probe_su_backend_finto_ritorna_esito_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    seen: dict = {}
    _fake_urlopen_json(
        monkeypatch,
        {"success": True, "data": [{"url": "https://example.com/"}]},
        seen,
    )
    ok, count, detail = health_mod.probe("http://finto.invalid", "finta-chiave", 5.0)
    assert ok is True
    assert count == 1
    assert detail == "ok"
    assert seen["url"] == "http://finto.invalid/v2/search"
    assert seen["body"] == {"query": health_mod.DEFAULT_QUERY, "limit": 1}
    assert seen["auth"] == "Bearer finta-chiave"


def test_probe_su_backend_finto_ritorna_esiti_di_fallimento(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    # Risposta non andata a buon fine.
    _fake_urlopen_json(monkeypatch, {"success": False, "data": []})
    ok, count, detail = health_mod.probe("http://finto.invalid", "finta-chiave", 5.0)
    assert (ok, count, detail) == (False, 0, "Firecrawl returned an unsuccessful response")
    # Nessun risultato da example.com.
    _fake_urlopen_json(monkeypatch, {"success": True, "data": [{"url": "https://altro.invalid/"}]})
    ok, count, detail = health_mod.probe("http://finto.invalid", "finta-chiave", 5.0)
    assert ok is False
    assert detail == "search returned no result from example.com"
    # Errore di rete finto.
    _fake_urlopen_error(monkeypatch, urllib.error.URLError("finto giu"))
    ok, count, detail = health_mod.probe("http://finto.invalid", "finta-chiave", 5.0)
    assert ok is False and count == 0
    assert detail.startswith("request failed:")


def test_probe_stabile_a_parita_di_backend_finto(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    _fake_urlopen_json(monkeypatch, {"success": True, "data": [{"url": "https://example.com/"}]})
    first = health_mod.probe("http://finto.invalid", "finta-chiave", 5.0)
    second = health_mod.probe("http://finto.invalid", "finta-chiave", 5.0)
    assert first == second == (True, 1, "ok")


def test_probe_striscia_la_barra_finale_dell_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    seen: dict = {}
    _fake_urlopen_json(
        monkeypatch,
        {"success": True, "data": [{"url": "https://example.com/"}]},
        seen,
    )
    health_mod.probe("http://finto.invalid/", "finta-chiave", 5.0)
    assert seen["url"] == "http://finto.invalid/v2/search"


def test_helper_di_probe_fissati() -> None:
    assert health_mod.DEFAULT_QUERY == '"Example Domain" site:example.com'
    assert health_mod.extract_urls({"data": {"web": [{"url": "https://example.com/a"}]}}) == [
        "https://example.com/a"
    ]
    assert health_mod.extract_urls({"data": [{"url": "https://example.com/b"}]}) == [
        "https://example.com/b"
    ]
    assert health_mod.extract_urls({"data": None}) == []
    assert health_mod.is_expected_result("https://example.com/") is True
    assert health_mod.is_expected_result("https://sub.example.com/x") is True
    assert health_mod.is_expected_result("https://altro.invalid/") is False


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(health_mod.probe).parameters) == ["api_url", "api_key", "timeout"]
    assert list(inspect.signature(health_mod.read_cached_success).parameters) == [
        "path",
        "ttl_seconds",
        "now",
    ]
    assert list(inspect.signature(health_mod.extract_urls).parameters) == ["payload"]
    assert list(inspect.signature(health_mod.is_expected_result).parameters) == ["url"]
    assert list(inspect.signature(health_mod.write_success).parameters) == [
        "path",
        "now",
        "result_count",
    ]
    assert list(inspect.signature(health_mod.parse_args).parameters) == []
    assert list(inspect.signature(health_mod.main).parameters) == []


def test_unify_delega_senza_cambiare_esito(monkeypatch: pytest.MonkeyPatch) -> None:
    """Se firecrawl espone probe, deve dare lo stesso esito (wrapper che delega)."""
    _no_network(monkeypatch)
    import nexgen_core.tools.firecrawl as fc_mod

    if not hasattr(fc_mod, "probe"):
        pytest.skip("unify non ancora applicata: solo health espone probe")
    _fake_urlopen_json(monkeypatch, {"success": True, "data": [{"url": "https://example.com/"}]})
    expected = (True, 1, "ok")
    assert health_mod.probe("http://finto.invalid", "finta-chiave", 5.0) == expected
    _fake_urlopen_json(monkeypatch, {"success": True, "data": [{"url": "https://example.com/"}]})
    assert fc_mod.probe("http://finto.invalid", "finta-chiave", 5.0) == expected
    assert list(inspect.signature(fc_mod.probe).parameters) == ["api_url", "api_key", "timeout"]
