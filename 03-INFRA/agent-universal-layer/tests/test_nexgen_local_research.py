"""Test della ricerca persistente: sessioni, continuazioni, copertura, niente apply.

Nessun modello reale (ScriptedLLM), nessun account reale: i connettori sono
finti come nel resto della suite. Qui si verifica il contratto di fase 3:
la sessione ripresa conserva le fonti, dichiara le letture parziali e non
applica mai due volte (né una) la stessa proposta.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.research_graph import ResearchError, research_task
from nexgen_local.tools import ToolRegistry


class ScriptedLLM:
    def __init__(self, route=None, decisions=None, answers=None) -> None:
        self.route = route or {"source": "vault", "keywords": ["x"]}
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


class ComboRegistry(ToolRegistry):
    """Mail + Drive deterministici per il caso guida, con conteggio ricerche."""

    def __init__(self, cfg: LaneConfig, drive_text: str = "") -> None:
        super().__init__(cfg)
        self.searches: list[str] = []
        self._drive_text = drive_text or "Contratto: oggetto e clausole."

    def search_mail(self, query: str) -> str:
        self.searches.append(f"mail:{query}")
        return self._record("search_mail", {"query": query}, "m1 | ieri | a@b.cc | Budget")

    def read_mail(self, mid: str, offset: int = 0) -> str:
        if mid.strip() != "m1":
            return self._refuse("read_mail", {"id": mid}, "(rifiutato)")
        body = "La fattura scade lunedi'."
        out = f"[mail:m1]\nDa: a@b.cc\n\n{body}"
        self.last_coverage = {"tool": "read_mail", "args": {"id": mid}, "offset": 0,
                              "total": len(out), "truncated": False}
        return self._record("read_mail", {"id": mid}, out)

    def search_drive(self, query: str) -> str:
        self.searches.append(f"drive:{query}")
        return self._record("search_drive", {"query": query}, "d1 | Contratto.txt | text/plain | oggi\nd2 | Altro.txt | text/plain | oggi")

    def read_drive(self, file_id: str, offset: int = 0) -> str:
        if file_id.strip() not in ("d1", "d2"):
            return self._refuse("read_drive", {"id": file_id}, "(rifiutato)")
        name = "Contratto.txt" if file_id.strip() == "d1" else "Altro.txt"
        text = self._drive_text if file_id.strip() == "d1" else "Altro contenuto."
        out = f"[drive:{name} ({file_id.strip()})]\nTipo: text/plain\n\n{text}"
        total, start, width = len(out), max(0, offset), self.cfg.read_chars
        chunk = out[start:start + width]
        truncated = start + len(chunk) < total
        self.last_coverage = {"tool": "read_drive", "args": {"id": file_id}, "offset": start,
                              "total": total, "truncated": truncated}
        if truncated:
            chunk += f"\n[...troncato — continua da {start + len(chunk)} su {total}]"
        return self._record("read_drive", {"id": file_id, "name": name}, chunk)


def _cfg(tmp_path: Path, **over) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    args = dict(
        vault_root=vault, repo_roots=(), model="fake-model",
        audit_path=tmp_path / "audit.jsonl", mails_dir=tmp_path / "mails",
        uploads_dir=tmp_path / "uploads", research_dir=tmp_path / "research",
    )
    args.update(over)
    return LaneConfig(**args)


def test_guide_case_compares_mail_and_contract_then_drafts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il caso guida su tre interazioni: contratto, poi mail (pivot di fonte
    alla ripresa), poi risposta — bozza sì, invio mai, fonti conservate."""
    import nexgen_local.compose as compose_module
    import nexgen_local.research_graph as rg

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        compose_module, "apply_mail",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("apply chiamato in ricerca!")),
    )
    monkeypatch.setattr(rg, "ToolRegistry", lambda cfg: ComboRegistry(cfg))

    first = research_task(
        ScriptedLLM(
            route={"source": "drive", "keywords": ["contratto"]},
            decisions=[
                {"action": "search_drive", "arg": "contratto"},
                {"action": "read_drive", "arg": "d1"},
                {"action": "answer", "arg": ""},
            ],
            answers=["Il contratto dice X."],
        ),
        cfg, "Trova il contratto su Drive.",
    )
    assert first["status"] == "answer"

    second = research_task(
        ScriptedLLM(
            route={"source": "mail", "keywords": ["budget"]},
            decisions=[
                {"action": "search_mail", "arg": "budget"},
                {"action": "read_mail", "arg": "m1"},
                {"action": "answer", "arg": ""},
            ],
            answers=["La mail chiede Y, il contratto dice X."],
        ),
        cfg, "Ora trova la mail sul budget e confrontala col contratto.",
        session_id=first["session_id"],
    )
    assert second["status"] == "answer"
    targets = [item["target"] for item in second["reads"]]
    assert "d1" in targets and "m1" in targets, "entrambe le fonti restano nella sessione"

    third = research_task(
        ScriptedLLM(
            route={"source": "mail", "keywords": ["budget"]},
            decisions=[
                {"action": "draft_mail", "arg": ""},
                {"action": "answer", "arg": ""},
            ],
            answers=["Confermo tenendo conto del contratto.", "Risposta pronta con confronto."],
        ),
        cfg, "Rispondi alla mail tenendo conto del confronto.",
        session_id=first["session_id"],
    )
    assert third["status"] == "answer"
    assert third["collected_proposals"]["mail_draft"], "la bozza deve esistere"
    assert len(list((tmp_path / "mails").glob("*.json"))) == 1, "una sola bozza, mai duplicata"
    assert "d1" in third["status_block"] and "m1" in third["status_block"]

    # Continuare ancora non duplica la proposta né applica.
    fourth = research_task(
        ScriptedLLM(
            route={"source": "mail", "keywords": ["budget"]},
            decisions=[{"action": "answer", "arg": ""}],
            answers=["Confermo, bozza invariata."],
        ),
        cfg, "Va bene cosi'.",
        session_id=first["session_id"],
    )
    assert fourth["status"] == "answer"
    assert len(list((tmp_path / "mails").glob("*.json"))) == 1
    assert fourth["collected_proposals"]["mail_draft"] == third["collected_proposals"]["mail_draft"]


def test_continue_opens_second_result_without_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Apri il secondo': legge d2 senza una nuova ricerca."""
    import nexgen_local.research_graph as rg

    cfg = _cfg(tmp_path)
    registry_holder: dict = {"regs": []}

    def factory(cfg):
        registry = ComboRegistry(cfg)
        registry_holder["regs"].append(registry)
        return registry

    monkeypatch.setattr(rg, "ToolRegistry", factory)
    llm = ScriptedLLM(
        route={"source": "drive", "keywords": ["contratto"]},
        decisions=[
            {"action": "search_drive", "arg": "contratto"},
            {"action": "read_drive", "arg": "d1"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Il contratto dice X."],
    )
    first = research_task(llm, cfg, "Trova il contratto su Drive.")
    assert first["status"] == "answer"
    assert len(registry_holder["regs"]) == 1
    assert registry_holder["regs"][0].searches != []

    llm2 = ScriptedLLM(
        route={"source": "drive", "keywords": ["contratto"]},
        decisions=[
            {"action": "read_drive", "arg": "d2"},
            {"action": "answer", "arg": ""},
        ],
        answers=["L'altro dice Y, confrontato con X."],
    )
    second = research_task(llm2, cfg, "Apri il secondo documento.", session_id=first["session_id"])
    assert second["status"] == "answer"
    assert len(registry_holder["regs"]) == 2
    assert registry_holder["regs"][1].searches == [], "nessuna nuova ricerca"
    assert any(r["target"] == "d2" for r in second["reads"])
    assert any(r["target"] == "d1" for r in second["reads"]), "la prima fonte resta"


def test_continuation_declares_partial_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Documento troncato: seconda finestra completa, copertura dichiarata."""
    import nexgen_local.research_graph as rg

    cfg = _cfg(tmp_path, read_chars=40)
    monkeypatch.setattr(rg, "ToolRegistry", lambda cfg: ComboRegistry(cfg))
    llm = ScriptedLLM(
        route={"source": "drive", "keywords": ["contratto"]},
        decisions=[
            {"action": "search_drive", "arg": "contratto"},
            {"action": "read_drive", "arg": "d1"},
            {"action": "continue_read", "arg": ""},
            {"action": "answer", "arg": ""},
        ],
        answers=["Letto tutto il contratto."],
    )
    summary = research_task(llm, cfg, "Leggi tutto il contratto.")
    assert summary["status"] == "answer"
    assert "parziale" in summary["status_block"]
    assert "completa" in summary["status_block"]


def test_unknown_session_refuses_without_model(tmp_path: Path) -> None:
    """Sessione inesistente: rifiuto prima di qualsiasi chiamata al modello."""
    cfg = _cfg(tmp_path)

    class CountingLLM(ScriptedLLM):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def json(self, system: str, user: str):
            self.calls += 1
            return self.route

    llm = CountingLLM()
    with pytest.raises(ResearchError, match="inesistente"):
        research_task(llm, cfg, "continua", session_id="nope")
    assert llm.calls == 0


def _confab_llm() -> ScriptedLLM:
    return ScriptedLLM(
        route={"source": "vault", "keywords": ["gatti"]},
        decisions=[
            {"action": "search_vault", "arg": "gatti"},
            {"action": "read_file", "arg": "gatti.md"},
            {"action": "answer", "arg": ""},
        ],
        answers=[
            "Ho eseguito i test. File aggiornato e test superati.",
            "Ho eseguito i test. File aggiornato e verificato.",
        ],
    )


def _write_gatti(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "gatti.md").write_text("Nota sui gatti.\n", encoding="utf-8")


def test_confabulation_is_surfaced_not_silent(tmp_path: Path) -> None:
    """Risposta inventata: problems/confabulation nel summary, non successo pulito."""
    _write_gatti(tmp_path)
    cfg = _cfg(tmp_path)
    summary = research_task(_confab_llm(), cfg, "Leggi la nota gatti.")
    assert summary["status"] == "answer"
    assert summary["problems"], "il verificatore ha rilevato, il summary deve riportare"
    assert summary["confabulation"] is True


def test_cli_persistent_confabulation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Stesso caso da CLI: avviso su stderr e uscita 1, non 0."""
    import argparse

    from nexgen_local import cli as cli_module

    _write_gatti(tmp_path)
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(cli_module, "_config", lambda args: cfg)
    monkeypatch.setattr(cli_module, "_llm", lambda cfg: _confab_llm())
    args = argparse.Namespace(task="Leggi la nota gatti.", session_id="new", max_steps=6, json=False)
    assert cli_module.cmd_explore(args) == 1
    assert "ATTENZIONE" in capsys.readouterr().err


def test_mcp_new_starts_persistent_session(tmp_path: Path) -> None:
    """Da MCP session_id='new' avvia una sessione, non 'inesistente'."""
    from nexgen_local.mcp_server import tool_explore

    _write_gatti(tmp_path)
    cfg = _cfg(tmp_path)
    llm = ScriptedLLM(
        route={"source": "vault", "keywords": ["gatti"]},
        decisions=[
            {"action": "search_vault", "arg": "gatti"},
            {"action": "read_file", "arg": "gatti.md"},
            {"action": "answer", "arg": ""},
        ],
        answers=["I gatti nella nota."],
    )
    out = tool_explore(cfg, llm, "Leggi la nota gatti.", session_id="new")
    assert "(rifiutato" not in out
    assert "[sessione:" in out
    assert list((tmp_path / "research").glob("research-*.sqlite")), "checkpoint persistito"


def test_steps_budget_is_per_interaction(tmp_path: Path) -> None:
    """Prima interazione al tetto, ripresa con budget fresco: legge davvero."""
    _write_gatti(tmp_path)
    cfg = _cfg(tmp_path)
    first = research_task(
        ScriptedLLM(
            route={"source": "vault", "keywords": ["gatti"]},
            decisions=[
                {"action": "search_vault", "arg": "gatti"},
                {"action": "read_file", "arg": "gatti.md"},
                {"action": "answer", "arg": ""},
            ],
            answers=["I gatti."],
        ),
        cfg, "Leggi la nota gatti.", max_steps=3,
    )
    assert first["status"] == "answer"
    second = research_task(
        ScriptedLLM(
            route={"source": "vault", "keywords": ["gatti"]},
            decisions=[
                {"action": "read_file", "arg": "gatti.md"},
                {"action": "answer", "arg": ""},
            ],
            answers=["Riletto i gatti."],
        ),
        cfg, "Rileggi la nota.", session_id=first["session_id"], max_steps=3,
    )
    assert second["status"] == "answer", "il budget riparte a ogni interazione"
    assert second["reads"], "la ripresa ha letto davvero"


def test_research_storage_is_private(tmp_path: Path) -> None:
    """Directory 700 e sqlite 600: i checkpoint nominano mail e corpi."""
    import os
    import stat

    if os.name == "nt":
        pytest.skip("permessi POSIX non applicabili su Windows")
    _write_gatti(tmp_path)
    cfg = _cfg(tmp_path)
    research_task(
        ScriptedLLM(
            route={"source": "vault", "keywords": ["gatti"]},
            decisions=[
                {"action": "search_vault", "arg": "gatti"},
                {"action": "read_file", "arg": "gatti.md"},
                {"action": "answer", "arg": ""},
            ],
            answers=["I gatti."],
        ),
        cfg, "Leggi la nota gatti.",
    )
    research_dir = tmp_path / "research"
    assert stat.S_IMODE(research_dir.stat().st_mode) == 0o700
    files = list(research_dir.glob("research-*.sqlite"))
    assert files, "checkpoint creato"
    for path in files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
