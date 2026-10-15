"""Pinning dello split research/close di jobs prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con job e modelli
finti: job_research finto completa con risposta e ricevute, job_close finto
chiude con bozza strutturata, job sconosciuto rifiutato con errore chiaro.
Solo tmp_path, solo backend finti, nessuna rete, nessuna azione reale.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.jobs import JobError, detect_job, job_close, job_research
from nexgen_local.tools import ToolRegistry


class FakeLLM:
    """Modello finto: risposte prescritte, mai rete."""

    def __init__(self, route: dict | None = None, answers: list[str] | None = None) -> None:
        self.route = route
        self.answers = list(answers or ["sintesi finta"])
        self.seen_users: list[str] = []

    def json(self, system: str, user: str) -> dict | None:
        return self.route

    def text(self, system: str, user: str) -> str:
        self.seen_users.append(user)
        return self.answers.pop(0) if self.answers else "sintesi finta"


class FakeWebRegistry(ToolRegistry):
    """Registry con web finto e connettori neutralizzati, mai rete."""

    def __init__(self, cfg: LaneConfig, web_text: str) -> None:
        super().__init__(cfg)
        self._web_text = web_text

    def web_search(self, query: str) -> str:
        return self._record("web_search", {"query": query}, self._web_text, status="ok")

    def search_mail(self, query: str) -> str:
        return self._record("search_mail", {"query": query}, "(nessun risultato)", status="empty")

    def read_mail(self, mid: str) -> str:
        return self._record("read_mail", {"id": mid}, "(nessun risultato)", status="empty")

    def search_drive(self, query: str) -> str:
        return self._record("search_drive", {"query": query}, "(nessun risultato)", status="empty")

    def read_drive(self, file_id: str) -> str:
        return self._record("read_drive", {"id": file_id}, "(nessun risultato)", status="empty")

    def search_calendar(self, query: str) -> str:
        return self._record("search_calendar", {"query": query}, "(nessun risultato)", status="empty")

    def read_calendar(self, event_id: str) -> str:
        return self._record("read_calendar", {"id": event_id}, "(nessun risultato)", status="empty")

    def search_outlook(self, query: str) -> str:
        return self._record("search_outlook", {"query": query}, "(nessun risultato)", status="empty")

    def read_outlook(self, mid: str) -> str:
        return self._record("read_outlook", {"id": mid}, "(nessun risultato)", status="empty")


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


def test_fake_research_completes_with_answer(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e un progetto dimostrativo.\n")
    tools = FakeWebRegistry(cfg, "Risultato web su Airone Blu.\n")
    llm = FakeLLM(answers=["## Cosa dicono le fonti\n- Airone Blu [01-NOTE/airone.md] [web]"])
    result = job_research(llm, tools, cfg, "progetto Airone Blu")
    assert result.job == "research"
    assert result.answer, "la ricerca finta completa con una risposta"
    assert "Airone Blu" in result.answer
    names = [receipt["tool"] for receipt in result.receipts]
    assert "search_vault" in names
    assert "read_vault" in names
    assert "web_search" in names
    assert any(source.startswith("web:") for source in result.sources)
    assert result.confabulation is False


def test_fake_close_completes_with_draft(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "04-NOW" / "sessione.md", "Abbiamo deciso X. Domanda aperta Y.\n")
    llm = FakeLLM(
        route={
            "title": "Sessione di prova",
            "summary": "Due righe di esito.",
            "decisions": ["Tenere la lane in sola lettura"],
            "open_questions": ["Quando i mestieri?"],
            "links": ["04-NOW/sessione.md"],
        }
    )
    result = job_close(llm, ToolRegistry(cfg), cfg, "04-NOW/sessione.md")
    assert result.job == "close"
    assert result.draft, "la chiusura finta produce una bozza"
    assert "## Decisioni" in result.draft
    assert "Tenere la lane in sola lettura" in result.draft
    assert result.answer == result.draft
    assert result.sources == ["04-NOW/sessione.md"]
    assert result.receipts, "almeno una ricevuta di lettura registrata"


def test_unknown_job_rejected_with_clear_error(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert detect_job("dimmi che ore sono") is None
    with pytest.raises(JobError, match="tema vuoto"):
        job_research(FakeLLM(), ToolRegistry(cfg), cfg, "   ")
    with pytest.raises(JobError, match="fuori dalle radici|inesistente"):
        job_close(FakeLLM(route={"title": "x"}), ToolRegistry(cfg), cfg, "../fuori.md")
    import nexgen_local.jobs as jobs_mod

    if hasattr(jobs_mod, "run_job"):
        with pytest.raises(JobError, match="sconosciuto"):
            jobs_mod.run_job(FakeLLM(), ToolRegistry(cfg), cfg, "sconosciuto", "qualcosa")


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(job_research).parameters) == ["llm", "tools", "cfg", "topic"]
    assert list(inspect.signature(job_close).parameters) == ["llm", "tools", "cfg", "session_path", "save"]
    assert inspect.signature(job_close).parameters["save"].default is False
    assert list(inspect.signature(detect_job).parameters) == ["task"]


def test_research_close_helpers_in_sequence() -> None:
    import nexgen_local.jobs as jobs_mod

    src = Path(jobs_mod.__file__).read_text(encoding="utf-8")
    assert "def _run_research_job" in src
    assert "def _run_close_job" in src
    assert src.index("def _run_research_job") < src.index("def _run_close_job")
    research_body = src[src.index("def job_research") : src.index("def _run_close_job")]
    assert "_run_research_job(" in research_body
    close_pos = src.index("def job_close")
    if hasattr(jobs_mod, "run_job"):
        run_pos = src.index("def run_job")
        close_body = src[close_pos:run_pos]
        run_body = src[run_pos:]
        assert "_run_close_job(" in close_body
        assert "_run_research_job(" in run_body
        assert "_run_close_job(" in run_body
        assert run_body.index("_run_research_job(") < run_body.index("_run_close_job(")
    else:
        close_body = src[close_pos:]
        assert "_run_close_job(" in close_body
