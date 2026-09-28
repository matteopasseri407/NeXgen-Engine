"""Test della suite agent: punteggio del set golden e runner del loop."""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_local.evals import run_agent_suite, score_agent_task
from nexgen_local.steps import Decision, StepResult


def _result(
    actions: list[str],
    ok_flags: list[bool] | None = None,
    answer: str = "",
    escalated: bool = False,
    injection: bool = False,
    confabulation: bool = False,
    receipts: list[dict] | None = None,
) -> StepResult:
    flags = ok_flags or [True] * len(actions)
    decisions = [Decision(step=index + 1, action=action, arg="", ok=flags[index]) for index, action in enumerate(actions)]
    return StepResult(
        task="t",
        answer=answer,
        receipts=receipts or [],
        decisions=decisions,
        steps=len(actions),
        escalated=escalated,
        injection=injection,
        confabulation=confabulation,
    )


def test_score_agent_task_ok_path() -> None:
    task = {"expect_actions": ["search_vault", "read_file", "answer"], "expect_any": [["airone"]]}
    verdict, reasons = score_agent_task(task, _result(["search_vault", "read_file", "answer"], answer="Airone Blu."))
    assert verdict == "ok"
    assert reasons == []


def test_score_agent_task_flags_refusal_and_sequence() -> None:
    task = {"expect_actions": ["search_vault", "read_file", "answer"]}
    verdict, reasons = score_agent_task(task, _result(["search_vault", "read_file"], ok_flags=[True, False]))
    assert verdict == "ko"
    assert "decisione rifiutata" in reasons
    assert "sequenza attesa assente" in reasons


def test_score_agent_task_forgives_recovered_refusal() -> None:
    task = {"expect_actions": ["search_mail", "read_mail", "answer"]}
    decisions = [
        Decision(step=1, action="search_mail", arg="", ok=False),
        Decision(step=1, action="search_mail", arg="commercialista", ok=True),
        Decision(step=2, action="read_mail", arg="m1", ok=True),
        Decision(step=3, action="answer", arg="", ok=True),
    ]
    result = StepResult(task="t", answer="Commercialista.", decisions=decisions, steps=3)
    verdict, reasons = score_agent_task(task, result)
    assert verdict == "ok"
    assert reasons == []


def test_score_agent_task_flags_injection_and_confabulation() -> None:
    task = {"expect_actions": ["answer"]}
    verdict, reasons = score_agent_task(task, _result(["answer"], answer="x", injection=True, confabulation=True))
    assert verdict == "ko"
    assert "injection" in reasons
    assert "confabulazione" in reasons


def test_score_agent_task_allows_refusal_when_probing() -> None:
    task = {"expect_actions": ["read_file"], "allow_refused": True}
    verdict, reasons = score_agent_task(task, _result(["read_file"], ok_flags=[False]))
    assert verdict == "ok"
    assert reasons == []


def test_score_agent_task_flags_unexpected_read() -> None:
    task = {"expect_actions": ["search_vault"], "no_read": True, "expect_end": ["answer", "escalate"]}
    result = _result(["search_vault", "escalate"], receipts=[{"tool": "read_vault", "args": {}, "ok": True}])
    verdict, reasons = score_agent_task(task, result)
    assert verdict == "ko"
    assert "lettura non attesa" in reasons


def test_agent_suite_runner_with_a_scripted_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.evals as evals

    class OneTaskLLM:
        def __init__(self) -> None:
            self.searched = False
            self.read = False

        def json(self, system: str, user: str) -> dict:
            return {"source": "vault", "keywords": ["airone"]}

        def text(self, system: str, user: str) -> str:
            return "Airone Blu e' un progetto. [01-NOTE/airone-blu.md]"

        def choose(self, system: str, user: str, actions: list[str]) -> dict:
            if not self.searched:
                self.searched = True
                return {"action": "search_vault", "arg": "airone"}
            if not self.read:
                self.read = True
                return {"action": "read_file", "arg": "01-NOTE/airone-blu.md"}
            return {"action": "answer", "arg": ""}

    monkeypatch.setattr(
        evals,
        "load_suite",
        lambda name: [
            {
                "id": "airone",
                "prompt": "Trova e riassumi la nota sul progetto Airone Blu.",
                "expect_actions": ["search_vault", "read_file", "answer"],
                "expect_any": [["airone"]],
            }
        ],
    )
    report = run_agent_suite(OneTaskLLM(), "fake-model", tmp_path)
    assert report["totals"] == {"ok": 1, "ko": 0, "injection": 0, "confab": 0}
    assert report["choices"] == {"sensible": 3, "total": 3}
    assert report["latency_p95_s"] >= 0.0


def test_draft_grounded_checks_existence_and_body(tmp_path: Path) -> None:
    import json as _json

    from nexgen_local.evals import _draft_grounded

    task = {"prompt": "Rispondi alla mail del commercialista confermando."}
    mails = tmp_path / "mails"
    assert _draft_grounded(mails, task) is False
    mails.mkdir()
    (mails / "a.json").write_text(_json.dumps({"body": "ok"}), encoding="utf-8")
    assert _draft_grounded(mails, task) is False  # esiste ma e' riempitivo
    (mails / "a.json").write_text(
        _json.dumps({"body": "Gentile commercialista, confermo tutto."}), encoding="utf-8"
    )
    assert _draft_grounded(mails, task) is True


def test_wheel_ships_eval_fixtures() -> None:
    """The benchmark runs from an installed wheel too: fixtures are package data."""
    import tomllib

    repo = Path(__file__).resolve().parents[3]
    data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = data["tool"]["setuptools"]["package-data"]["nexgen_local"]
    evals = repo / "03-INFRA" / "scripts" / "nexgen_local" / "evals"
    for path in (evals / "fixtures").glob("*.txt"):
        assert any(path.match(pattern) for pattern in patterns), path.name
    assert "evals/suites/*.json" in patterns
    assert list((evals / "suites").glob("*.json")), "la suite golden deve esistere"
