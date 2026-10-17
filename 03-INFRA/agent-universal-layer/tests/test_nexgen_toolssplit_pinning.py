"""Pinning dello split tools prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con backend finti:
ricerca vault sintetica ritorna risultati, lettura calendario finta ritorna
eventi, strumento sconosciuto rifiutato con errore chiaro. Solo backend
finti, solo tmp_path, nessuna rete, nessuna scrittura fuori tmp.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.tools import ToolRegistry


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir()
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unreachable(*args, **kwargs):
        raise AssertionError("rete toccata")

    monkeypatch.setattr(ToolRegistry, "_run", staticmethod(_unreachable))
    import nexgen_local.connectors.calendar as calendar_conn
    import nexgen_local.connectors.drive as drive_conn

    monkeypatch.setattr(calendar_conn, "_default_http", _unreachable)
    monkeypatch.setattr(drive_conn, "_default_http", _unreachable)


def _fake_calendar(monkeypatch: pytest.MonkeyPatch) -> dict:
    import nexgen_local.connectors.calendar as calendar_conn

    event = {
        "id": "e1",
        "summary": "Dentista controllo",
        "description": "controllo annuale",
        "location": "Studio Rossi",
        "start": {"dateTime": "2026-10-01T10:00:00+02:00"},
        "end": {"dateTime": "2026-10-01T11:00:00+02:00"},
    }
    monkeypatch.setattr(calendar_conn, "list_events", lambda *a, **k: [dict(event)])
    monkeypatch.setattr(calendar_conn, "get_event", lambda *a, **k: dict(event))
    return event


def test_vault_search_returns_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    cfg = _cfg(tmp_path)
    (cfg.vault_root / "dentista.md").write_text("# Dentista\ncontrollo annuale\n", encoding="utf-8")
    tools = ToolRegistry(cfg)
    out = tools.call("search_vault", {"query": "dentista"})
    assert "dentista.md" in out
    assert tools.calls[-1].ok
    assert tools.calls[-1].status == "ok"


def test_calendar_read_returns_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    _fake_calendar(monkeypatch)
    tools = ToolRegistry(_cfg(tmp_path))
    found = tools.call("search_calendar", {"query": "Dentista"})
    assert "Dentista" in found
    assert tools.calls[-1].ok
    detail = tools.call("read_calendar", {"id": "e1"})
    assert "Dentista" in detail
    assert "[calendar:e1]" in detail
    assert tools.calls[-1].ok


def test_unknown_tool_rejected_with_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    tools = ToolRegistry(_cfg(tmp_path))
    out = tools.call("strumento_inesistente", {})
    assert "strumento sconosciuto" in out
    assert "strumento_inesistente" in out
    assert not tools.calls[-1].ok
    assert tools.calls[-1].status == "error"
    result = tools.call_result("altro_sconosciuto", {})
    assert result.status == "error" and not result.usable


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(ToolRegistry.call).parameters) == ["self", "name", "args"]
    assert list(inspect.signature(ToolRegistry.search_vault).parameters) == [
        "self", "query", "require_all",
    ]
    assert list(inspect.signature(ToolRegistry.read_vault).parameters) == ["self", "path", "offset"]
    assert list(inspect.signature(ToolRegistry.search_calendar).parameters) == ["self", "query"]
    assert list(inspect.signature(ToolRegistry.read_calendar).parameters) == ["self", "event_id", "offset"]
    assert list(inspect.signature(ToolRegistry.search_drive).parameters) == ["self", "query"]
    assert list(inspect.signature(ToolRegistry.read_drive).parameters) == ["self", "file_id", "offset"]


def test_dispatch_split_handlers_in_sequence() -> None:
    src = Path(ToolRegistry.__module__ and __import__("nexgen_local.tools", fromlist=["x"]).__file__).read_text(
        encoding="utf-8"
    )
    assert "def _handle_vault" in src
    assert "def _handle_calendar" in src
    assert "def _handle_drive" in src
    body = src[src.index("def call("):]
    assert body.index("_handle_vault") < body.index("_handle_calendar")
    assert body.index("_handle_calendar") < body.index("_handle_drive")
