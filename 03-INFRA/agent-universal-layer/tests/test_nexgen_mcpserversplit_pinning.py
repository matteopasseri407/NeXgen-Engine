"""Pinning dello split mcp_server prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con server e LLM
finti: build_server registra i tool attesi, tool_explore finto ritorna
l'esito a passi con ricevute, tool_ask finto ritorna risposta e ricevute.
Solo tmp_path, solo finti, nessuna rete, nessuna porta reale.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.mcp_server import build_server, tool_ask, tool_explore
from nexgen_local.tools import ToolRegistry


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(repo,),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unreachable(*args, **kwargs):
        raise AssertionError("rete toccata")

    monkeypatch.setattr(ToolRegistry, "_run", staticmethod(_unreachable))


class FakeAskLLM:
    def __init__(self, route: dict | None = None, answers: list[str] | None = None) -> None:
        self.route = route
        self.answers = list(answers or ["risposta finta"])

    def json(self, system: str, user: str) -> dict | None:
        return self.route

    def text(self, system: str, user: str) -> str:
        return self.answers.pop(0) if self.answers else "risposta finta"


class FakeExploreLLM:
    def __init__(self) -> None:
        self.calls = 0

    def json(self, system: str, user: str) -> dict | None:
        return {"source": "vault", "keywords": ["airone"]}

    def text(self, system: str, user: str) -> str:
        return "Airone Blu e' un progetto [01-NOTE/airone.md]."

    def choose(self, system: str, user: str, actions: list[str]) -> dict | None:
        self.calls += 1
        if self.calls == 1 and "search_vault" in actions:
            return {"action": "search_vault", "arg": "airone", "why": "cerco"}
        if "answer" in actions:
            return {"action": "answer", "arg": "", "why": "rispondo"}
        return {"action": actions[0], "arg": "", "why": "proseguo"}


def _tool_names(server) -> list[str]:
    return [t.name for t in asyncio.run(server.list_tools())]


def test_build_server_registra_tool_attesi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mcp")
    _no_network(monkeypatch)
    server = build_server(_cfg(tmp_path), llm_factory=lambda: FakeAskLLM())
    assert set(_tool_names(server)) == {
        "lane_ask",
        "lane_explore",
        "lane_research",
        "lane_close",
        "lane_status",
    }


def test_build_server_senza_ask_nasconde_solo_lane_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mcp")
    _no_network(monkeypatch)
    server = build_server(_cfg(tmp_path), llm_factory=lambda: FakeAskLLM(), include_ask=False)
    names = set(_tool_names(server))
    assert "lane_ask" not in names
    assert {"lane_explore", "lane_research", "lane_close", "lane_status"} <= names


def test_tool_explore_finto_ritorna_esito(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto.\n")
    out = tool_explore(cfg, FakeExploreLLM(), "Cerca la nota su Airone Blu.")
    assert "[passo 1: search_vault airone [ok]]" in out
    assert "[ricevute:" in out


def test_tool_ask_finto_ritorna_risposta_e_ricevute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_network(monkeypatch)
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto.\n")
    llm = FakeAskLLM(
        route={"source": "vault", "keywords": ["airone"]},
        answers=["Airone Blu e' un progetto."],
    )
    out = tool_ask(cfg, llm, "Cerca la nota su Airone Blu.")
    assert "Airone" in out
    assert "[ricevute:" in out


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(build_server).parameters) == ["cfg", "llm_factory", "include_ask"]
    assert list(inspect.signature(tool_explore).parameters) == [
        "cfg",
        "llm",
        "task",
        "max_steps",
        "session_id",
    ]
    assert list(inspect.signature(tool_ask).parameters) == ["cfg", "llm", "question"]
