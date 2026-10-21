"""La ripresa del compito: il grafo richiama i passi veri, il checkpoint sa dove si era fermato.

Il repository è vero, perché il passo `unità` legge il ramo e la copia; i passi
che aprirebbero una scheda o un cancello sono finti e registrano le chiamate, così
il test misura una cosa sola: quali passi il grafo rifà e quali salta. L'identità
del giro è quella vera, hash compresi.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexgen_local import cli
from nexgen_local import supervision_gates as gates
from nexgen_local import supervision_graph as graph
from nexgen_local import supervision_review as review
from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane"


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "sandbox")


@pytest.fixture
def task(lane):
    return state.start("prova ripresa")["id"]


@pytest.fixture
def unit(task, repo, tmp_path):
    """L'unità già creata, come dopo `sup unit`: la ripresa parte da lì."""
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief dell'unità prova\n\nSolo fatti.\n", encoding="utf-8")
    worktree.create(
        task, "prova", repo=repo.path, base="main", brief=str(brief),
        allow=["docs/*.md"], verify=f'"{_python()}" -c "print(1)"',
    )
    return "prova"


def _python() -> str:
    import sys

    return sys.executable


class Steps:
    """I passi finti: registrano cosa è stato chiesto e scrivono solo il diario che serve."""

    def __init__(self, verdict: str = "PASS") -> None:
        self.verdict = verdict
        self.run_calls: list[str] = []
        self.collect_calls: list[str] = []
        self.publish_calls: list[dict] = []

    def run(self, task_id: str, unit: str) -> dict:
        self.run_calls.append(unit)
        state.append_event(task_id, "unit_started", unit=unit, harness="opencode", session="ses_finta", mode="run")
        return {"unit": unit, "mode": "run", "session": "ses_finta", "pid": 1, "row": {}}

    def collect(self, task_id: str, unit: str) -> dict:
        self.collect_calls.append(unit)
        record = state.unit_record(task_id, unit)
        head = gates.head_of(Path(record["worktree"]), record["branch"])
        state.append_event(task_id, "unit_collected", unit=unit, verdict=self.verdict, head=head)
        return {
            "unit": unit, "verdict": self.verdict, "base": record["base"], "head": head,
            "gates": [gates.GateResult("confine", self.verdict == "PASS", "prova del test")],
            "package": Path("pacchetto"), "raw": Path("grezzo"),
        }

    def publish(self, task_id: str, unit: str, **kwargs) -> dict:
        self.publish_calls.append({"unit": unit, **kwargs})
        return {
            "unit": unit, "branch": kwargs["branch"], "repo": kwargs["repo"], "pushed": False,
            "commits": [{"published": "a" * 12, "source": "b" * 12, "subject": "prova"}],
        }


@pytest.fixture
def steps(monkeypatch):
    fake = Steps()
    monkeypatch.setattr(graph.harness, "run_unit", fake.run)
    monkeypatch.setattr(graph.gates, "collect", fake.collect)
    monkeypatch.setattr(graph.publish_module, "publish", fake.publish)
    return fake


def _steps_of(result: dict) -> list[str]:
    return [row["step"] for row in result["trace"]]


# --- il primo giro e la ripresa ---------------------------------------------


def test_the_first_resume_runs_the_open_steps_and_stops_at_the_review(task, unit, steps):
    result = graph.resume(task)

    assert _steps_of(result) == ["unità", "esecuzione", "raccolta", "revisione"]
    assert result["stop_reason"] == "waiting_review"
    assert steps.run_calls == ["prova"]
    assert steps.collect_calls == ["prova"]
    assert steps.publish_calls == []
    assert "sup review" in result["message"]


def test_a_second_resume_does_not_send_the_work_or_collect_again(task, unit, steps):
    graph.resume(task)
    before = (len(steps.run_calls), len(steps.collect_calls))

    result = graph.resume(task)

    assert (len(steps.run_calls), len(steps.collect_calls)) == before
    assert result["stop_reason"] == "waiting_review"
    assert _steps_of(result) == ["unità", "esecuzione", "raccolta", "revisione", "revisione"]


def test_the_checkpoint_and_the_identity_live_in_the_task_folder(task, unit, steps):
    graph.resume(task)

    folder = state.open_task(task)
    assert (folder / graph.CHECKPOINT_FILE).is_file()
    identity = json.loads((folder / graph.IDENTITY_FILE).read_text(encoding="utf-8"))
    assert identity["unit"] == "prova"
    assert identity["objective_hash"] == graph.identity(task, "prova")["objective_hash"]
    assert identity["brief_hash"] == graph.identity(task, "prova")["brief_hash"]


# --- i rifiuti dell'identità ------------------------------------------------


def test_a_changed_brief_is_refused_before_any_step_runs(task, unit, steps):
    graph.resume(task)
    before = (len(steps.run_calls), len(steps.collect_calls))
    brief = state.open_task(task) / "20-brief-prova.md"
    brief.write_text(brief.read_text(encoding="utf-8") + "\nuna riga in più\n", encoding="utf-8")

    with pytest.raises(graph.GraphError) as exc:
        graph.resume(task)

    assert exc.value.kind == "brief_mismatch"
    assert (len(steps.run_calls), len(steps.collect_calls)) == before


def test_a_different_unit_is_refused_instead_of_continuing_the_saved_one(task, unit, repo, tmp_path, steps):
    graph.resume(task)
    brief = tmp_path / "seconda.md"
    brief.write_text("# Brief della seconda unità\n", encoding="utf-8")
    worktree.create(
        task, "seconda", repo=repo.path, base="main", brief=str(brief),
        allow=["docs/*.md"], verify=f'"{_python()}" -c "print(1)"',
    )

    with pytest.raises(graph.GraphError) as exc:
        graph.resume(task, unit="seconda")

    assert exc.value.kind == "unit_mismatch"


def test_a_task_without_a_unit_has_nothing_to_resume(task, steps):
    with pytest.raises(graph.GraphError) as exc:
        graph.resume(task)

    assert exc.value.kind == "no_unit"
    assert steps.run_calls == []


def test_restarting_throws_the_saved_run_away(task, unit, steps):
    graph.resume(task)
    brief = state.open_task(task) / "20-brief-prova.md"
    brief.write_text(brief.read_text(encoding="utf-8") + "\ncorretto\n", encoding="utf-8")

    result = graph.resume(task, restart=True)

    assert _steps_of(result)[0] == "unità"
    assert result["stop_reason"] == "waiting_review"


# --- dopo la revisione ------------------------------------------------------


def test_an_approved_unit_waits_for_the_owners_checkout(task, unit, steps):
    graph.resume(task)
    review.approve(task, "prova", "il confine è rispettato")

    result = graph.resume(task)

    assert result["stop_reason"] == "waiting_publish"
    assert steps.publish_calls == []
    assert "--repo" in result["message"]


def test_the_approved_unit_is_published_when_the_checkout_is_named(task, unit, repo, steps):
    graph.resume(task)
    review.approve(task, "prova", "il confine è rispettato")

    result = graph.resume(task, repo=str(repo.path), branch="main")

    assert result["stop_reason"] == "done"
    assert len(steps.publish_calls) == 1
    assert steps.publish_calls[0]["branch"] == "main"


def test_a_finished_run_reruns_nothing(task, unit, repo, steps):
    graph.resume(task)
    review.approve(task, "prova", "va bene")
    graph.resume(task, repo=str(repo.path), branch="main")
    before = (len(steps.run_calls), len(steps.collect_calls), len(steps.publish_calls))

    result = graph.resume(task)

    assert result["stop_reason"] == "done"
    assert (len(steps.run_calls), len(steps.collect_calls), len(steps.publish_calls)) == before


def test_a_rework_goes_back_to_the_executor_and_publishes_nothing(task, unit, tmp_path, steps):
    graph.resume(task)
    corrections = tmp_path / "correzioni.md"
    corrections.write_text("Rifai il paragrafo due.\n", encoding="utf-8")
    review.rework(task, "prova", corrections, sender=lambda *a, **k: {"mode": "queued", "reason": "occupata"})

    result = graph.resume(task)

    assert result["stop_reason"] == "rework"
    assert steps.publish_calls == []
    assert steps.collect_calls == ["prova"]


def test_a_failed_collection_stops_and_names_the_gate(task, unit, steps):
    steps.verdict = "FAIL"

    result = graph.resume(task)

    assert result["stop_reason"] == "failed"
    assert "confine: prova del test" in result["message"]


# --- il verbo ---------------------------------------------------------------


def test_the_verb_prints_every_step_and_where_it_stopped(task, unit, steps, capsys):
    assert cli.main(["sup", "resume", task]) == 0

    out = capsys.readouterr().out
    assert "unità: unità prova" in out
    assert "revisione: in attesa della revisione" in out
    assert "stato: in attesa della revisione" in out
    assert graph.CHECKPOINT_FILE in out


def test_the_verb_exits_non_zero_when_a_step_failed(task, unit, steps):
    steps.verdict = "FAIL"

    assert cli.main(["sup", "resume", task]) == 1
