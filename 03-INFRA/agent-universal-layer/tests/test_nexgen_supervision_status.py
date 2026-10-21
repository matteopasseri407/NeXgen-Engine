"""Come una unità e un compito si leggono in `sup status`: gli stessi campi, la stessa frase.

Il costo di un'unità è quello che la sua ultima raccolta ha misurato, e quello
del compito è la stessa misura: la frase è quella di `sup collect`, e un numero
che nessuno ha misurato si dice a parole, mai come zero.
"""
from __future__ import annotations

import pytest

from nexgen_local import cli
from nexgen_local import supervision_state as state
from nexgen_local import supervision_status as status


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


def _unit(task: str, name: str = "prova") -> None:
    state.append_event(
        task, "unit_created", unit=name, worktree="C:/copia", branch="sup/x",
        base="0" * 40, allow=[], verify="", repo="C:/copia",
    )


def _collected(task: str, name: str = "prova", **cost) -> None:
    state.append_event(
        task, "unit_collected", unit=name, verdict="PASS", head="a" * 40,
        cost={
            "round_usd": 0.1, "unit_usd": 0.5, "task_usd": 0.9, "model": "opencode-go/x",
            "session": "ses_uno", "stopped": True, "note": "",
        } | cost,
    )


def test_a_collected_unit_shows_the_same_sentence_as_collect(lane):
    task = state.start("prova stato costi")["id"]
    _unit(task)
    _collected(task)

    unit = state.status(task)["units"][0]

    assert "costo: giro 0.1000 $, unità 0.5000 $, compito 0.9000 $ (modello opencode-go/x)" in status.unit_line(unit)


def test_the_task_line_says_where_that_number_was_measured(lane):
    task = state.start("prova stato costi")["id"]
    _unit(task)
    _collected(task)

    assert status.task_line(state.status(task)) == (
        "costi: compito 0.9000 $ (modello opencode-go/x, misurato alla raccolta di prova)"
    )


def test_a_unit_collected_before_its_cost_was_saved_reads_the_ledger(lane):
    """L'unità non porta il numero, ma il registro dei costi sì: si mostra, dicendo da dove."""
    task = state.start("prova unità storica")["id"]
    _unit(task)
    state.append_event(task, "unit_collected", unit="prova", verdict="PASS", head="a" * 40)
    for value in (0.000077, 0.782949):
        state.append_jsonl(lane / task / state.COSTS_FILE, {
            "unit": "prova", "role": "esecutore", "session": "ses_uno", "cost_usd": value,
        })

    unit = state.status(task)["units"][0]

    assert "costo: unità 0.7829 $ (dal registro dei costi)" in status.unit_line(unit)


def test_a_unit_never_collected_says_the_cost_was_not_measured(lane):
    task = state.start("prova stato senza raccolta")["id"]
    _unit(task)

    assert "costo: non misurato (nessuna raccolta)" in status.unit_line(state.status(task)["units"][0])


def test_a_collection_without_a_price_says_why(lane):
    task = state.start("prova stato prezzo ignoto")["id"]
    _unit(task)
    _collected(task, round_usd=None, unit_usd=None, task_usd=None, note="l'export non risponde")

    assert "costo: non misurato (l'export non risponde)" in status.unit_line(state.status(task)["units"][0])
    assert "compito non misurato (l'export non risponde)" in status.task_line(state.status(task))


def test_before_any_collection_the_task_line_keeps_the_ledger_and_its_unknowns(lane):
    task = state.start("prova stato senza fonte")["id"]
    state.append_jsonl(lane / task / state.COSTS_FILE, {"unit": "prova", "session": "ses_x", "cost_usd": None})

    assert status.task_line(state.status(task)) == "costi: compito non disponibile (1 riga, 1 senza fonte)"


def test_status_prints_the_cost_of_the_unit_and_of_the_task(lane, capsys):
    task = state.start("prova stato stampa")["id"]
    _unit(task)
    _collected(task)
    capsys.readouterr()

    assert cli.main(["sup", "status", task]) == 0

    out = capsys.readouterr().out
    assert "costo: giro 0.1000 $, unità 0.5000 $, compito 0.9000 $ (modello opencode-go/x)" in out
    assert "costi: compito 0.9000 $ (modello opencode-go/x, misurato alla raccolta di prova)" in out
