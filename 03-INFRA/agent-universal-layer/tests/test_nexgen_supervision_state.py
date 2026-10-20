"""La cartella del compito: file attesi, diario in append, stato, id sconosciuto.

Questo modulo non avvia processi: i test non ne aprono nessuno.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexgen_local import supervision_state as state


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """La lane vive in una cartella temporanea: nessun test scrive nello stato vero."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


def _folder(lane: Path, task_id: str) -> Path:
    return lane / task_id


def test_start_creates_the_folder_and_its_files(lane):
    started = state.start("Porta la fase 1 dentro il motore")
    folder = _folder(lane, started["id"])

    assert started["id"].endswith("-porta-la-fase-1-dentro-il-motore")
    assert Path(started["path"]) == folder
    assert sorted(path.name for path in folder.iterdir()) == [
        state.OBJECTIVE_FILE,
        state.JOURNAL_FILE,
        state.BRIEF_TEMPLATE,
        state.REPORT_TEMPLATE,
    ]
    objective = (folder / state.OBJECTIVE_FILE).read_text(encoding="utf-8")
    assert "Porta la fase 1 dentro il motore" in objective
    assert f"id: {started['id']}" in objective


def test_the_brief_template_carries_every_field_the_plan_asks_for(lane):
    started = state.start("prova modelli")
    brief = (_folder(lane, started["id"]) / state.BRIEF_TEMPLATE).read_text(encoding="utf-8")
    for field in (
        "## Identificativo e ruolo",
        "## Obiettivo",
        "## Contesto minimo",
        "## Risultato atteso",
        "## Confine",
        "## Verifica",
        "## Fatto quando",
        "## Cosa non fare",
        "## Formato del resoconto atteso",
    ):
        assert field in brief


def test_the_report_template_carries_every_field_the_plan_asks_for(lane):
    started = state.start("prova modelli")
    report = (_folder(lane, started["id"]) / state.REPORT_TEMPLATE).read_text(encoding="utf-8")
    for field in (
        "## Stato",
        "## Commit sul ramo locale",
        "## File toccati",
        "## Comandi eseguiti",
        "## Test nuovi",
        "## Dubbi e richieste al proprietario",
        "## Prossimo passo se bloccato",
    ):
        assert field in report


def test_two_starts_in_the_same_second_get_their_own_folder(lane):
    first = state.start("prova unica", now=1_000_000)
    second = state.start("prova unica", now=1_000_000)
    assert first["id"] != second["id"]
    assert _folder(lane, first["id"]).is_dir()
    assert _folder(lane, second["id"]).is_dir()


def test_the_journal_is_append_only(lane):
    task_id = state.start("prova diario")["id"]
    state.append_event(task_id, "nota", testo="prima")
    state.append_event(task_id, "nota", testo="seconda")

    lines = (_folder(lane, task_id) / state.JOURNAL_FILE).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert [row["event"] for row in rows] == ["started", "nota", "nota"]
    assert [row["testo"] for row in rows[1:]] == ["prima", "seconda"]
    assert all(row["task"] == task_id and row["at"] for row in rows)


def test_status_reads_what_the_task_holds(lane):
    task_id = state.start("prova stato")["id"]
    state.append_event(
        task_id, "attached", unit="unita-uno", role="esecutore", harness="opencode", session="ses_uno",
    )
    state.append_jsonl(
        _folder(lane, task_id) / state.COSTS_FILE,
        {"cost_usd": 0.25, "tokens_in": 100, "tokens_out": 20, "unit": "unita-uno"},
    )
    state.append_jsonl(
        _folder(lane, task_id) / state.COSTS_FILE,
        {"cost_usd": None, "tokens_in": None, "tokens_out": None, "note": "export non riuscito"},
    )

    status = state.status(task_id)

    assert status["id"] == task_id
    assert status["objective"] == "prova stato"
    assert [unit["unit"] for unit in status["units"]] == ["unita-uno"]
    assert status["units"][0]["session"] == "ses_uno"
    assert status["sessions"] == ["ses_uno"]
    assert status["last_event"]["event"] == "attached"
    assert status["events"] == 2
    assert status["cost_rows"] == 2
    assert status["cost_total_usd"] == 0.25
    assert status["cost_unknown_rows"] == 1


def test_a_task_without_cost_rows_has_no_total(lane):
    task_id = state.start("prova senza costi")["id"]
    status = state.status(task_id)
    assert status["cost_rows"] == 0
    assert status["cost_total_usd"] is None, "zero è un costo misurato, non l'assenza di una fonte"


def test_status_refuses_an_unknown_task(lane):
    with pytest.raises(state.SupervisionError, match="compito inesistente"):
        state.status("20260101-000000-mai-creato")


def test_an_id_that_escapes_the_root_is_refused(lane):
    with pytest.raises(state.SupervisionError, match="non valido"):
        state.status("../altro")


def test_the_objective_survives_a_lost_journal(lane):
    task_id = state.start("prova recupero")["id"]
    (_folder(lane, task_id) / state.JOURNAL_FILE).unlink()
    assert state.objective(task_id) == "prova recupero"


def test_list_tasks_is_oldest_first(lane):
    names = [state.start("prova elenco", now=1_000_000 + offset)["id"] for offset in (0, 60, 120)]
    listed = state.list_tasks()
    assert [task["id"] for task in listed] == names
    assert [task["objective"] for task in listed] == ["prova elenco"] * 3


def test_a_start_without_an_objective_is_refused(lane):
    with pytest.raises(state.SupervisionError, match="obiettivo vuoto"):
        state.start("   ")
