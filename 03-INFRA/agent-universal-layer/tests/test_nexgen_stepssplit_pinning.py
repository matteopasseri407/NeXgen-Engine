"""Pinning dello split di steps.py: decide/finish/loop restano identici.

Solo policy e modelli finti, nessuna rete, nessuna azione reale.
Fissa il comportamento attuale prima dello split in tre driver:
- decide_step su stato finto ritorna l'azione attesa,
- finish_answer chiude con risposta e contenuto raccolto,
- run_steps con budget zero si ferma subito.
"""
from __future__ import annotations

import time
from pathlib import Path

from nexgen_local.config import LaneConfig
from nexgen_local.steps import (
    Candidate,
    LoopState,
    StepResult,
    decide_step,
    finish_answer,
    run_steps,
)
from nexgen_local.tools import ToolRegistry


class FakeLLM:
    def __init__(
        self,
        route: dict | None = None,
        decisions: list[dict] | None = None,
        answers: list[str] | None = None,
    ) -> None:
        self.route = route or {"source": "none", "keywords": [], "path": ""}
        self.decisions = list(decisions or [])
        self.answers = list(answers or ["Risposta finta e concisa."])
        self.choose_users: list[str] = []

    def json(self, system: str, user: str) -> dict | None:
        return self.route

    def text(self, system: str, user: str) -> str:
        return self.answers.pop(0) if self.answers else "Risposta finta e concisa."

    def choose(self, system: str, user: str, actions: list[str]) -> dict | None:
        self.choose_users.append(user)
        if not self.decisions:
            return {"action": "escalate", "arg": "", "why": ""}
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


def test_decide_step_su_stato_finto_ritorna_azione_attesa(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    state = LoopState(task="domanda semplice", route="none")
    menu = [Candidate("answer"), Candidate("escalate")]
    llm = FakeLLM(decisions=[{"action": "answer", "arg": "", "why": "fatto"}])
    result = StepResult(task="domanda semplice")
    decided = decide_step(llm, cfg, state, menu, result, time.time())
    assert decided == ("answer", "")
    assert result.decisions[-1].ok is True
    assert result.decisions[-1].action == "answer"


def test_finish_answer_chiude_con_ricevuta(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    tools = ToolRegistry(cfg)
    task = "domanda semplice"
    state = LoopState(task=task, route="none")
    state.reads = ["[nota]\nContenuto finto per la risposta."]
    result = StepResult(task=task)
    from nexgen_local.step_state import Decision

    result.decisions.append(Decision(step=1, action="answer", arg="", why=""))
    route = {"source": "none"}
    llm = FakeLLM(answers=["Risposta finta e concisa."])
    step_started = time.time()
    finish_answer(llm, tools, cfg, state, route, task, [], result, step_started)
    assert result.answer == "Risposta finta e concisa."
    assert "Contenuto finto" in result.collected
    assert result.problems == []
    assert result.confabulation is False


def test_run_steps_con_budget_zero_si_ferma_subito(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    llm = FakeLLM()
    tools = ToolRegistry(cfg)
    result = run_steps(llm, tools, cfg, "compito vuoto", max_steps=0)
    assert result.escalated is True
    assert result.steps == 0
    assert len(result.decisions) == 1
    assert result.decisions[0].detail == "cap"
    assert result.receipts == []
    assert llm.choose_users == []
