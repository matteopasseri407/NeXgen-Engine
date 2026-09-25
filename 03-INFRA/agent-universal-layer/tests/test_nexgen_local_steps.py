"""Test del loop agentico limitato: menu, provenienza, retry, tetti, trappole.

Il modello e' un fake che restituisce decisioni prescritte: qui si verifica il
contratto del motore, non il modello. Nessun framework serve.
"""
from __future__ import annotations

import json
from pathlib import Path

from nexgen_local.config import LaneConfig
from nexgen_local.steps import LoopState, build_menu, run_steps
from nexgen_local.tools import ToolRegistry


class ScriptedLLM:
    def __init__(self, route: dict | None = None, decisions: list | None = None, answers: list[str] | None = None) -> None:
        self.route = route or {"source": "vault", "keywords": ["airone"]}
        self.decisions = list(decisions or [])
        self.answers = list(answers or ["risposta finta"])
        self.choose_users: list[str] = []

    def json(self, system: str, user: str) -> dict | None:
        return self.route

    def text(self, system: str, user: str) -> str:
        return self.answers.pop(0) if self.answers else "risposta finta"

    def choose(self, system: str, user: str, actions: list[str]) -> dict | None:
        self.choose_users.append(user)
        if not self.decisions:
            return {"action": "escalate", "arg": ""}
        return self.decisions.pop(0)


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


def _audit_lines(cfg: LaneConfig) -> list[dict]:
    return [json.loads(line) for line in cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()]


def test_loop_search_read_answer(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto dimostrativo.\n")
    llm = ScriptedLLM(
        route={"source": "vault", "keywords": ["airone"]},
        decisions=[
            {"action": "search_vault", "arg": "airone blu"},
            {"action": "read_file", "arg": "01-NOTE/airone.md"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Airone Blu e' un progetto dimostrativo. [01-NOTE/airone.md]"],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    assert result.escalated is False
    assert "Airone" in result.answer
    assert [decision.action for decision in result.decisions] == ["search_vault", "read_file", "answer"]
    assert result.steps == 3
    assert result.problems == []
    assert [receipt["tool"] for receipt in result.receipts] == ["search_vault", "read_vault"]


def test_invalid_output_gets_one_repair(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    llm = ScriptedLLM(decisions=[{"action": "boh"}, {"action": "escalate", "arg": ""}])
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Domanda.")
    assert result.escalated is True
    assert [decision.action for decision in result.decisions] == ["escalate"]
    assert len(llm.choose_users) == 2  # la prima risposta e' stata riparata una volta


def test_two_invalid_outputs_escalate(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    llm = ScriptedLLM(decisions=[{"action": "boh"}, {"action": "boh"}])
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Domanda.")
    assert result.escalated is True
    assert result.decisions[-1].ok is False
    assert "non valido" in result.decisions[-1].detail


def test_read_file_must_come_from_the_menu_candidates(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu.\n")
    llm = ScriptedLLM(
        route={"source": "vault", "keywords": ["airone"]},
        decisions=[{"action": "read_file", "arg": "/etc/passwd"}],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Leggi 01-NOTE/airone.md e riassumila.")
    assert result.escalated is True
    assert result.decisions[-1].ok is False
    assert "candidati" in result.decisions[-1].detail
    assert len(llm.choose_users) == 1  # validazione fallita: nessun retry
    assert _audit_lines(cfg)[-1]["tool"] == "step_refused"


def test_query_with_content_terms_is_refused(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu. Il termine parolasegreta compare qui.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/airone.md"},
            {"action": "search_vault", "arg": "parolasegreta"},
        ]
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    assert result.escalated is True
    assert result.decisions[-1].ok is False
    assert "contenuto recuperato" in result.decisions[-1].detail


def test_repeated_query_is_refused_and_a_different_one_is_allowed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "zzzznulla"},
            {"action": "search_vault", "arg": "zzzznulla"},
            {"action": "escalate", "arg": ""},
        ]
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Cerca qualcosa che non esiste.")
    refused = [decision for decision in result.decisions if not decision.ok]
    assert refused and "troppo simile" in refused[0].detail

    cfg2 = _cfg(tmp_path / "secondo")
    _write(cfg2.vault_root / "zzzznulla.md", "contenuto\n")
    llm2 = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "zzzznulla"},
            {"action": "escalate", "arg": ""},
        ]
    )
    result2 = run_steps(llm2, ToolRegistry(cfg2), cfg2, "Cerca zzzznulla.")
    assert result2.decisions[0].ok is True


def test_empty_search_gets_one_retry_then_menu_narrows(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    first = LoopState(task="t", route="vault", receipts=[{"tool": "search_vault", "args": {}, "ok": False}], empty_streak=1)
    assert [candidate.action for candidate in build_menu(cfg, first)] == ["search_vault", "answer", "escalate"]
    second = LoopState(task="t", route="vault", receipts=[{"tool": "search_vault", "args": {}, "ok": False}], empty_streak=2)
    assert [candidate.action for candidate in build_menu(cfg, second)] == ["answer", "escalate"]


def test_cap_steps_escalates(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "a.md", "Airone Blu e' un progetto.\n")
    _write(cfg.vault_root / "01-NOTE" / "b.md", "Airone Blu, seconda nota di progetto.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/a.md"},
            {"action": "search_vault", "arg": "progetto blu"},
            {"action": "read_file", "arg": "01-NOTE/b.md"},
        ]
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.", max_steps=4)
    assert result.escalated is True
    assert result.decisions[-1].detail == "cap"


def test_route_none_offers_only_answer_or_escalate(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    menu = build_menu(cfg, LoopState(task="Che ore sono?", route="none"))
    assert [candidate.action for candidate in menu] == ["answer", "escalate"]


def test_answer_claim_check_runs_in_the_loop(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/airone.md"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Ho letto la nota fantasma.md e l'ho riassunta."],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    assert result.confabulation is True
    assert result.problems


def test_retry_with_a_generic_word_does_not_read_the_wrong_note(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto dimostrativo.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "Falco Rosso"},
            {"action": "search_vault", "arg": "progetto Falco"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Non ho trovato nessuna nota su Falco Rosso."],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Trova e riassumi la nota sul progetto Falco Rosso.")
    tools_called = [receipt["tool"] for receipt in result.receipts]
    assert "read_vault" not in tools_called
    assert result.escalated is False
    assert "Non ho trovato" in result.answer


def test_prompt_keeps_only_recent_observations(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "a.md", "Airone Blu e' un progetto.\n")
    _write(cfg.vault_root / "01-NOTE" / "b.md", "Airone Blu, seconda nota di progetto.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/a.md"},
            {"action": "search_vault", "arg": "progetto blu"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Ok [01-NOTE/a.md]"],
    )
    run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    last = llm.choose_users[-1]
    assert "passi precedenti: 1" in last
    assert last.count("Osservazioni recenti") == 1
