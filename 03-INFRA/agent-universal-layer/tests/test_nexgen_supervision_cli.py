"""I verbi `sup` dalla riga di comando, con un harness finto.

`sup start`, `sup status`, `sup attach` e `sup costi` girano davvero, ma su una
lane temporanea e con `opencode` sostituito da un JSON letterale: nessuna
sessione vera viene letta e nessuna finestra aperta.
"""
from __future__ import annotations

import json

import pytest

from nexgen_local import cli
from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_state as state

EXPORT = {
    "info": {
        "id": "ses_cli",
        "model": {"id": "deepseek-v4.1-flash", "providerID": "opencode-go", "variant": "max"},
        "cost": 0.25,
        "tokens": {"input": 1000, "output": 200, "reasoning": 30, "cache": {"read": 400, "write": 0}},
        "time": {"created": 1_000_000, "updated": 1_060_000},
    },
    "messages": [],
}


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """Lane temporanea e harness finto: i verbi girano senza toccare il mondo vero."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    monkeypatch.setattr(costs.shutil, "which", lambda name: r"C:\fake\opencode.cmd")
    monkeypatch.setattr(
        costs.subprocess, "run",
        lambda argv, **kwargs: costs.subprocess.CompletedProcess(argv, 0, json.dumps(EXPORT), ""),
    )
    return tmp_path / "lane" / "supervision"


def _only_task(lane) -> str:
    return sorted(path.name for path in lane.iterdir() if path.is_dir())[0]


def test_start_prints_the_id_and_the_folder(lane, capsys):
    assert cli.main(["sup", "start", "prova fase 1"]) == 0

    out = capsys.readouterr().out
    task_id = _only_task(lane)
    assert task_id in out
    assert str(lane / task_id) in out
    assert (lane / task_id / state.OBJECTIVE_FILE).is_file()


def test_status_lists_the_tasks_without_an_id(lane, capsys):
    cli.main(["sup", "start", "primo compito"])
    capsys.readouterr()

    assert cli.main(["sup", "status"]) == 0

    assert "primo compito" in capsys.readouterr().out


def test_one_corrupted_journal_does_not_hide_the_other_tasks(lane, capsys):
    """Un diario illeggibile è un fatto di quel compito, non un guasto dell'elenco."""
    cli.main(["sup", "start", "compito sano"])
    healthy = _only_task(lane)
    cli.main(["sup", "start", "compito rotto"])
    broken = sorted(task for task in (path.name for path in lane.iterdir()) if task != healthy)[0]
    (lane / broken / state.JOURNAL_FILE).write_text('{"event": "started"}\nnon-json\n', encoding="utf-8")
    capsys.readouterr()

    assert cli.main(["sup", "status"]) == 0

    out = capsys.readouterr().out
    assert "compito sano" in out
    assert f"{broken}  (illeggibile:" in out
    assert "JSON non leggibile" in out

    assert cli.main(["sup", "status", broken]) == 1
    assert "JSON non leggibile" in capsys.readouterr().err
    assert cli.main(["sup", "status", healthy]) == 0


def test_start_status_attach_and_costi_end_to_end(lane, capsys):
    assert cli.main(["sup", "start", "prova fase 1"]) == 0
    task_id = _only_task(lane)
    capsys.readouterr()

    assert cli.main([
        "sup", "attach", task_id, "prova",
        "--role", "esecutore", "--opencode-session", "ses_cli",
    ]) == 0
    attached = capsys.readouterr().out
    assert "agganciata prova · esecutore · opencode ses_cli" in attached
    assert "0.2500 $" in attached

    assert cli.main(["sup", "costi", task_id]) == 0
    total = capsys.readouterr().out
    assert f"compito {task_id}" in total
    assert "1 riga, 0.2500 $" in total
    assert "1000 token in, 200 token out" in total

    assert cli.main(["sup", "status", task_id]) == 0
    status = capsys.readouterr().out
    assert "obiettivo: prova fase 1" in status
    assert "unità prova · esecutore · opencode ses_cli" in status
    assert "sessioni harness: ses_cli" in status
    assert "costi: 0.2500 $ (1 riga)" in status


def test_status_names_a_cost_it_could_not_measure(lane, capsys, monkeypatch):
    cli.main(["sup", "start", "prova senza fonte"])
    task_id = _only_task(lane)
    monkeypatch.setattr(costs.shutil, "which", lambda name: None)
    capsys.readouterr()

    assert cli.main([
        "sup", "attach", task_id, "prova",
        "--role", "esecutore", "--opencode-session", "ses_cli",
    ]) == 0
    assert "costo non disponibile" in capsys.readouterr().out

    assert cli.main(["sup", "status", task_id]) == 0
    status = capsys.readouterr().out
    assert "non disponibile (1 riga, 1 senza fonte)" in status


def test_an_unknown_task_fails_loudly(lane, capsys):
    assert cli.main(["sup", "status", "20260101-000000-mai-creato"]) == 1
    assert "compito inesistente" in capsys.readouterr().err

    assert cli.main(["sup", "costi", "20260101-000000-mai-creato"]) == 1
    assert "compito inesistente" in capsys.readouterr().err


def test_an_empty_objective_fails_loudly(lane, capsys):
    assert cli.main(["sup", "start", "   "]) == 1
    assert "obiettivo vuoto" in capsys.readouterr().err


def test_attach_refuses_an_id_it_would_splice_into_a_command(lane, capsys):
    """`ses_x&calc` non è una sessione: il verbo si ferma prima di avviare `opencode`."""
    cli.main(["sup", "start", "prova id"])
    task_id = _only_task(lane)
    capsys.readouterr()

    assert cli.main([
        "sup", "attach", task_id, "prova",
        "--role", "esecutore", "--opencode-session", "ses_x&calc",
    ]) == 1

    assert "sessione non valido" in capsys.readouterr().err
    assert not (lane / task_id / state.COSTS_FILE).exists()
    assert [row["event"] for row in state.read_events(task_id)] == ["started"], "niente aggancio nel diario"


def test_the_sup_verbs_answer_for_help(lane, capsys):
    for argv in (["sup", "--help"], ["sup", "start", "--help"], ["sup", "attach", "--help"]):
        with pytest.raises(SystemExit) as exit_info:
            cli.main(argv)
        assert exit_info.value.code == 0
