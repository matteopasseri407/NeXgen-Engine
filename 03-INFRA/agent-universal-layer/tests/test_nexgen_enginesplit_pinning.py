"""Pinning dello split route/retrieve/answer di engine prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con route e modelli
finti: sanitize_route pulisce una rotta sospetta, run_lane su rotta finta
ritorna l'esito atteso, retrieve con backend finto ritorna evidenza.
Solo tmp_path, solo backend finti, nessuna rete, nessuna azione reale.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from nexgen_local.config import LaneConfig
from nexgen_local.engine import retrieve, run_lane, sanitize_route
from nexgen_local.tools import ToolRegistry


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _FakeLLM:
    """Modello finto: rotta e risposte prescritte, mai rete."""

    def __init__(self, route: dict | None = None, answers: list[str] | None = None) -> None:
        self.route = route or {"source": "none"}
        self.answers = list(answers or ["risposta finta"])

    def json(self, system: str, user: str) -> dict | None:
        return self.route

    def text(self, system: str, user: str) -> str:
        return self.answers.pop(0) if self.answers else "risposta finta"

    def choose(self, system: str, user: str, actions: list[str]) -> dict | None:
        return None


def test_sanitize_route_cleans_suspicious_route(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    route = sanitize_route(
        {"source": "vault", "keywords": ["BDO Italia"], "path": "colloquio con BDO Italia"},
        cfg,
        "nota sul colloquio BDO",
    )
    assert route["path"] == ""
    assert route["source"] == "vault"
    assert route["keywords"] == ["BDO", "Italia"]


def test_run_lane_on_fake_route_returns_expected(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    result = run_lane(
        _FakeLLM(route={"source": "none"}, answers=["risposta finta"]),
        ToolRegistry(cfg),
        cfg,
        "Ciao, come stai?",
    )
    assert result.route["source"] == "none"
    assert result.collected == ""
    assert result.answer == "risposta finta"
    assert result.receipts == []
    assert result.problems == []


def test_retrieve_with_fake_backend_returns_evidence(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone-blu.md", "Airone Blu e' un progetto dimostrativo.\n")
    tools = ToolRegistry(cfg)
    route = {"source": "vault", "keywords": ["airone", "blu"], "path": "", "root": "", "fallback": False}
    collected = retrieve(tools, cfg, route, "Cerca la nota su Airone Blu e riassumila.")
    assert "dimostrativo" in collected
    names = [call.name for call in tools.calls]
    assert "search_vault" in names and "read_vault" in names
    assert any(call.ok for call in tools.calls)


def test_public_signatures_unchanged() -> None:
    import nexgen_local.engine as engine_mod

    assert list(inspect.signature(engine_mod.sanitize_route).parameters) == ["route", "cfg", "task"]
    assert list(inspect.signature(engine_mod.route_task).parameters) == ["llm", "cfg", "task"]
    assert list(inspect.signature(engine_mod.retrieve).parameters) == ["tools", "cfg", "route", "task"]
    assert list(inspect.signature(engine_mod.answer_task).parameters) == [
        "llm", "cfg", "task", "collected", "source", "sources",
    ]
    assert list(inspect.signature(engine_mod.run_lane).parameters) == [
        "llm", "tools", "cfg", "task", "canaries",
    ]


def test_route_retrieve_answer_helpers_in_sequence() -> None:
    import nexgen_local.engine as engine_mod

    src = Path(engine_mod.__file__).read_text(encoding="utf-8")
    assert "def _sanitize_route" in src
    assert "def _retrieve_evidence" in src
    assert "def _answer_from_evidence" in src
    body = src[src.index("def run_lane"):]
    assert body.index("_sanitize_route(") < body.index("_retrieve_evidence(")
    assert body.index("_retrieve_evidence(") < body.index("_answer_from_evidence(")
