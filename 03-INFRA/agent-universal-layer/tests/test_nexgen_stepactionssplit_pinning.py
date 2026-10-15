"""Pinning dello split step_actions prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con azioni e
backend finti: azione vault finta eseguita con osservazione, azione
calendario finta eseguita, sorgente sconosciuta rifiutata con errore
chiaro. Solo backend finti, solo tmp_path, nessuna rete, nessuna azione reale.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.step_actions import execute_action
from nexgen_local.step_state import LoopState
from nexgen_local.tools import ToolError, ToolRegistry, ToolResult


class _NoModel:
    def text(self, *args, **kwargs):
        raise AssertionError("il modello non deve essere chiamato")

    def json(self, *args, **kwargs):
        raise AssertionError("il modello non deve essere chiamato")

    def choose(self, *args, **kwargs):
        raise AssertionError("il modello non deve essere chiamato")


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
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


def _fake_backends(tools: ToolRegistry, monkeypatch: pytest.MonkeyPatch, routes: dict) -> list:
    seen: list = []

    def _fake(self, name: str, args: dict):
        assert self is tools
        seen.append((name, dict(args)))
        if name in routes:
            return routes[name]
        raise AssertionError(f"backend finto non previsto: {name}")

    monkeypatch.setattr(ToolRegistry, "call_result", _fake)
    return seen


def test_azione_vault_finta_eseguita_con_osservazione(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    tools = ToolRegistry(_cfg(tmp_path))
    _fake_backends(tools, monkeypatch, {"search_vault": ToolResult("nota finta sul dentista", "ok")})
    state = LoopState(task="domanda finta", route="none")
    execute_action(_NoModel(), tools, state, "search_vault", "dentista")
    assert state.tried_queries == ["dentista"]
    assert state.hits == ["nota finta sul dentista"]
    assert len(state.observations) == 1
    assert state.observations[0].startswith("search_vault(dentista) -> ")
    assert "nota finta" in state.observations[0]
    assert state.content_seen != []


def test_azione_calendario_finta_eseguita(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    tools = ToolRegistry(_cfg(tmp_path))
    body = "[calendar:e1]\nTitolo: Dentista finto"
    _fake_backends(tools, monkeypatch, {"read_calendar": ToolResult(body, "ok")})
    state = LoopState(task="domanda finta", route="none")
    execute_action(_NoModel(), tools, state, "read_calendar", "e1")
    assert len(state.calendar) == 1
    assert "Dentista finto" in state.calendar[0]
    assert state.read_ids == ["e1"]
    assert len(state.observations) == 1
    assert state.observations[0].startswith("read_calendar(e1) -> ")
    assert state.content_seen != []


def test_sorgente_sconosciuta_rifiutata_con_errore_chiaro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_network(monkeypatch)
    tools = ToolRegistry(_cfg(tmp_path))
    state = LoopState(task="domanda finta", route="none")
    with pytest.raises(ToolError, match="azione non eseguibile"):
        execute_action(_NoModel(), tools, state, "sorgente_sconosciuta", "x")
    with pytest.raises(ToolError, match="sorgente_sconosciuta"):
        execute_action(_NoModel(), tools, state, "sorgente_sconosciuta", "x")
    assert state.observations == []
    assert state.content_seen == []


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(execute_action).parameters) == ["llm", "tools", "state", "action", "arg"]


def test_dispatch_split_handlers_in_sequenza() -> None:
    import nexgen_local.step_actions as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "def _run_vault" in src
    assert "def _run_calendar" in src
    assert "def _run_drive" in src
    body = src[src.index("def execute_action("):]
    assert body.index("_run_vault") < body.index("_run_calendar")
    assert body.index("_run_calendar") < body.index("_run_drive")
