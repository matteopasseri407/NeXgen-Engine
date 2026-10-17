"""Pinning dello split research_graph: comportamento attuale su zad finti.

Solo modelli finti (ScriptedLLM), solo SQLite in tmp_path, nessuna rete,
nessun backend reale, timeout corti. Fissa che:
- un task finto completa con esito registrato,
- la continuazione dichiara copertura parziale,
- la sessione interrotta si rilascia per continuazione,
- la seconda ricerca concorrente e' rifiutata.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.research_graph import ResearchError, research_task


class ScriptedLLM:
    """Modello finto: route fissa, decisioni scriptate, risposte neutre."""

    def __init__(self, route=None, decisions=None, answers=None) -> None:
        self.route = route or {"source": "none", "keywords": []}
        self.decisions = list(decisions or [])
        self.answers = list(answers or ["risposta finta"])

    def json(self, system: str, user: str):
        return self.route

    def text(self, system: str, user: str) -> str:
        return self.answers.pop(0) if self.answers else "risposta finta"

    def choose(self, system: str, user: str, actions: list[str]):
        if not self.decisions:
            return {"action": "escalate", "arg": ""}
        return self.decisions.pop(0)


def _cfg(tmp_path: Path, **over) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    args = dict(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        mails_dir=tmp_path / "mails",
        uploads_dir=tmp_path / "uploads",
        research_dir=tmp_path / "research",
    )
    args.update(over)
    return LaneConfig(**args)


def _write_note(tmp_path: Path, name: str, content: str) -> None:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / name).write_text(content, encoding="utf-8")


def test_pinning_fake_task_completes_with_recorded_outcome(tmp_path: Path) -> None:
    _write_note(tmp_path, "gatti.md", "Nota sui gatti.\n")
    cfg = _cfg(tmp_path)
    summary = research_task(
        ScriptedLLM(
            route={"source": "vault", "keywords": ["gatti"]},
            decisions=[
                {"action": "search_vault", "arg": "gatti"},
                {"action": "read_file", "arg": "gatti.md"},
                {"action": "answer", "arg": ""},
            ],
            answers=["I gatti nella nota."],
        ),
        cfg,
        "Leggi la nota gatti.",
    )
    assert summary["status"] == "answer"
    assert summary["answer"], "esito registrato come risposta"
    assert summary["receipts"], "almeno una ricevuta registrata"
    assert summary["reads"], "almeno una lettura registrata"
    assert summary["session_id"], "sessione assegnata"
    assert list((tmp_path / "research").glob("research-*.sqlite")), "checkpoint persistito"


def test_pinning_continue_declares_partial_coverage(tmp_path: Path) -> None:
    _write_note(
        tmp_path,
        "contratto.md",
        "Contratto speciale con clausole importanti e dettagli finali extra.",
    )
    cfg = _cfg(tmp_path, read_chars=40)
    summary = research_task(
        ScriptedLLM(
            route={"source": "vault", "keywords": ["contratto"]},
            decisions=[
                {"action": "search_vault", "arg": "contratto"},
                {"action": "read_file", "arg": "contratto.md"},
                {"action": "continue_read", "arg": ""},
                {"action": "answer", "arg": ""},
            ],
            answers=["Letto tutto il contratto."],
        ),
        cfg,
        "Leggi tutto il contratto.",
    )
    assert summary["status"] == "answer"
    assert "parziale" in summary["status_block"], "prima finestra troncata dichiarata"
    assert "completa" in summary["status_block"], "seconda finestra completa dichiarata"


def test_pinning_interrupted_session_releases_for_continuation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    first = research_task(ScriptedLLM(route={"source": "none"}), cfg, "Prima istruzione.")

    class InterruptedLLM(ScriptedLLM):
        def choose(self, *args):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        research_task(
            InterruptedLLM(route={"source": "none"}),
            cfg,
            "Seconda istruzione.",
            session_id=first["session_id"],
        )
    third = research_task(
        ScriptedLLM(route={"source": "none"}, decisions=[{"action": "escalate", "arg": ""}]),
        cfg,
        "Terza istruzione.",
        session_id=first["session_id"],
    )
    assert third["session_id"] == first["session_id"]
    assert third["status"] == "escalate"


def test_pinning_concurrent_second_research_rejected(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    cfg = _cfg(tmp_path)
    first = research_task(ScriptedLLM(route={"source": "none"}), cfg, "Dimmi un saluto.")
    entered, release = Event(), Event()

    class BlockingLLM(ScriptedLLM):
        def choose(self, *args):
            entered.set()
            assert release.wait(5), "il test non ha rilasciato la prima interazione"
            return {"action": "escalate", "arg": ""}

    class UnusedLLM(ScriptedLLM):
        json_calls = 0

        def json(self, *args):
            self.json_calls += 1
            return {"source": "none"}

        def choose(self, *args):
            raise AssertionError("la sessione occupata non deve chiamare il modello")

    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(
            research_task,
            BlockingLLM(route={"source": "none"}),
            cfg,
            "Continua.",
            session_id=first["session_id"],
        )
        try:
            assert entered.wait(5)
            unused = UnusedLLM()
            with pytest.raises(ResearchError, match="sessione.*in uso"):
                research_task(unused, cfg, "Seconda istruzione.", session_id=first["session_id"])
            assert unused.json_calls == 0
            other = research_task(ScriptedLLM(route={"source": "none"}), cfg, "Sessione indipendente.")
            assert other["session_id"] != first["session_id"]
            assert not active.done()
        finally:
            release.set()
        assert active.result(timeout=5)["status"] == "escalate"

    assert research_task(
        ScriptedLLM(route={"source": "none"}),
        cfg,
        "Terza istruzione.",
        session_id=first["session_id"],
    )["session_id"] == first["session_id"]
