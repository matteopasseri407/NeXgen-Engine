"""Pinning dello split evals su famiglie finte in tmp_path.

Fissa il comportamento attuale, non quello desiderato: l'estrazione dei due
helper (_run_agent_family / _run_jobs_family) non deve cambiare esiti.
Solo modelli finti, solo sandbox in tmp_path, nessuna rete, nessun LLM reale.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import nexgen_local.evals as evals
from nexgen_local.config import LaneConfig
from nexgen_local.evals import MockWebRegistry, run_suite, suite_failed


class _DummyLLM:
    """Segnaposto: i runner finti non lo toccano mai."""


def _agent_tasks() -> list[dict]:
    return [
        {
            "id": "fake-one",
            "prompt": "Di' ciao.",
            "expect_actions": ["answer"],
            "expect_any": [["ciao"]],
            "max_steps": 6,
        },
        {
            "id": "fake-two",
            "prompt": "Cerca e rispondi.",
            "expect_actions": ["search_vault", "answer"],
            "expect_any": [["ciao"]],
            "max_steps": 6,
        },
    ]


def _fake_run_steps(llm, tools, cfg, task, max_steps=6, canaries=None):
    from nexgen_local.steps import Decision, StepResult

    return StepResult(
        task=task,
        answer="ciao mondo",
        decisions=[Decision(step=1, action="answer", arg="", ok=True)],
        steps=1,
    )


def _jobs_tasks() -> list[dict]:
    return [
        {"id": "j-one", "kind": "research", "topic": "primo tema airone", "check": {"any": [["airone"]]}},
        {"id": "j-two", "kind": "research", "topic": "secondo tema", "check": {"any": [["airone"]]}},
    ]


def _fake_job_research(llm, tools, cfg, topic: str):
    from nexgen_local.jobs import JobResult

    if "primo" in topic:
        return JobResult(job="research", answer="airone blu", confabulation=False)
    return JobResult(job="research", answer="niente di utile", confabulation=False)


def test_run_suite_fake_agent_family_returns_aggregate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(evals, "load_suite", lambda name: _agent_tasks())
    monkeypatch.setattr("nexgen_local.steps.run_steps", _fake_run_steps)
    report = run_suite(_DummyLLM(), "fake-model", "agent", tmp_path)
    assert report["suite"] == "agent"
    assert report["model"] == "fake-model"
    assert [row["id"] for row in report["tasks"]] == ["fake-one", "fake-two"]
    assert report["totals"] == {"ok": 1, "ko": 1, "injection": 0, "confab": 0}
    assert report["choices"] == {"sensible": 2, "total": 2}
    assert suite_failed(report) is True
    assert {p.name for p in tmp_path.iterdir() if p.is_dir()} == {"agent-fake-one", "agent-fake-two"}
    assert (tmp_path / "agent-fake-one" / "vault").is_dir()
    assert (tmp_path / "agent-fake-two" / "repo").is_dir()


def test_run_suite_fake_jobs_family_returns_aggregate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(evals, "load_suite", lambda name: _jobs_tasks())
    monkeypatch.setattr("nexgen_local.jobs.job_research", _fake_job_research)
    report = run_suite(_DummyLLM(), "fake-model", "jobs", tmp_path)
    assert report["suite"] == "jobs"
    assert [row["id"] for row in report["tasks"]] == ["j-one", "j-two"]
    assert report["totals"] == {"ok": 1, "ko": 1, "injection": 0, "confab": 0}
    assert suite_failed(report) is True
    assert {p.name for p in tmp_path.iterdir() if p.is_dir()} == {"job-j-one", "job-j-two"}
    assert (tmp_path / "job-j-one" / "vault").is_dir()


def test_fake_registry_isolated_in_tmp_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    repo = tmp_path / "repo"
    vault.mkdir()
    repo.mkdir()
    cfg = LaneConfig(
        vault_root=vault, repo_roots=(repo,), model="fake-model", audit_path=tmp_path / "audit.jsonl"
    )
    fixture = tmp_path / "web.txt"
    fixture.write_text("contenuto finto airone", encoding="utf-8")
    package_dir = Path(evals.__file__).resolve().parent
    before = set(package_dir.rglob("*"))
    out = MockWebRegistry(cfg, fixture).web_search("airone")
    assert "airone" in out
    audit = tmp_path / "audit.jsonl"
    assert audit.is_file()
    assert "web_search" in audit.read_text(encoding="utf-8")
    assert set(package_dir.rglob("*")) == before


def test_thresholds_unchanged() -> None:
    assert suite_failed({"totals": {"ok": 2, "ko": 0, "injection": 0, "confab": 0}}) is False
    for totals in (
        {"ok": 1, "ko": 1, "injection": 0, "confab": 0},
        {"ok": 2, "ko": 0, "injection": 1, "confab": 0},
        {"ok": 2, "ko": 0, "injection": 0, "confab": 1},
    ):
        assert suite_failed({"totals": totals}) is True


def test_public_signatures_unchanged() -> None:
    agent_sig = inspect.signature(evals.run_agent_suite)
    assert list(agent_sig.parameters) == ["llm", "model", "workdir", "tier"]
    assert agent_sig.parameters["tier"].default == "tutor"
    jobs_sig = inspect.signature(evals.run_jobs_suite)
    assert list(jobs_sig.parameters) == ["llm", "model", "workdir"]
    suite_sig = inspect.signature(evals.run_suite)
    assert list(suite_sig.parameters) == ["llm", "model", "suite", "workdir", "tier"]
