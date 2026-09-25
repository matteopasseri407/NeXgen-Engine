"""Test del server MCP: le funzioni dei tool e la costruzione del server.

Il server espone la lane agli agenti; qui si verifica il contenuto dei tool
con un modello finto, e che il server si costruisca con l'SDK MCP.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.mcp_server import build_server, tool_ask, tool_close, tool_research, tool_status
from nexgen_local.tools import ToolRegistry


class FakeLLM:
    def __init__(self, route: dict | None = None, answers: list[str] | None = None) -> None:
        self.route = route
        self.answers = list(answers or ["risposta finta"])

    def json(self, system: str, user: str) -> dict | None:
        return self.route

    def text(self, system: str, user: str) -> str:
        return self.answers.pop(0) if self.answers else "risposta finta"


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    return LaneConfig(
        vault_root=vault,
        repo_roots=(repo,),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        drafts_dir=tmp_path / "drafts",
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_tool_ask_returns_answer_and_receipts(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto.\n")
    llm = FakeLLM(route={"source": "vault", "keywords": ["airone"]}, answers=["Airone Blu e' un progetto."])
    out = tool_ask(cfg, llm, "Cerca la nota su Airone Blu.")
    assert "Airone" in out
    assert "[ricevute:" in out


def test_tool_research_returns_answer_and_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto.\n")
    monkeypatch.setattr(
        ToolRegistry, "web_search", lambda self, query: self._record("web_search", {"query": query}, "web finto")
    )
    llm = FakeLLM(answers=["## Cosa dicono le fonti\n- Airone Blu [01-NOTE/airone.md]"])
    out = tool_research(cfg, llm, "progetto Airone Blu")
    assert "Airone" in out
    assert "web_search" in out


def test_tool_close_returns_draft(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "sessione.md", "Abbiamo deciso X.\n")
    llm = FakeLLM(route={"title": "Sessione", "decisions": ["Tenere la lane in sola lettura"]})
    out = tool_close(cfg, llm, "sessione.md")
    assert "## Decisioni" in out
    assert "Tenere la lane in sola lettura" in out


def test_tool_status_lists_models_and_read_only(tmp_path: Path) -> None:
    cfg = LaneConfig(
        vault_root=tmp_path,
        repo_roots=(),
        model="base",
        router_model="router-x",
        answer_model="answer-y",
        audit_path=tmp_path / "audit.jsonl",
    )
    out = tool_status(cfg)
    assert "router: router-x" in out
    assert "answer: answer-y" in out
    assert "sola lettura" in out


def test_build_server_builds_with_a_fake_factory(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    server = build_server(_cfg(tmp_path), llm_factory=lambda: FakeLLM())
    assert server.name == "nexgen-local-lane"
