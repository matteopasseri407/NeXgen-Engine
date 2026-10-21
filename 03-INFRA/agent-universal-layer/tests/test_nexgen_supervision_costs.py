"""I costi di una sessione harness: export finto, riga attesa, aggregazione.

Nessun test chiama `opencode`: l'export è un JSON letterale restituito da un
`subprocess.run` finto, e l'eseguibile è un finto `which`.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_state as state

#: Un export con i valori che i test pretendono: niente si ricalcola qui dentro.
EXPORT = {
    "info": {
        "id": "ses_test",
        "model": {"id": "deepseek-v4.1-flash", "providerID": "opencode-go", "variant": "max"},
        "cost": 0.25,
        "tokens": {"input": 1000, "output": 200, "reasoning": 30, "cache": {"read": 400, "write": 0}},
        "outcome": "succeeded",
        "time": {"created": 1_000_000, "updated": 1_060_000, "idle": 1_060_000},
    },
    "messages": [],
}


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """La lane vive in una cartella temporanea: niente stato vero, niente CLI vere."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    monkeypatch.setattr(costs.shutil, "which", lambda name: r"C:\fake\opencode.cmd")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def task(lane):
    return state.start("prova costi")["id"]


@pytest.fixture
def export_returns(monkeypatch):
    """Fa rispondere `opencode` con un testo scelto dal test, senza avviarlo."""
    def apply(payload, code: int = 0) -> None:
        def fake_run(argv, **kwargs):
            stdout = json.dumps(payload) if isinstance(payload, dict) else payload
            return costs.subprocess.CompletedProcess(argv, code, stdout, "")

        monkeypatch.setattr(costs.subprocess, "run", fake_run)

    return apply


def test_a_session_export_becomes_the_row_the_plan_asks_for(lane, task, export_returns):
    export_returns(EXPORT)

    row = costs.record(task, "unita-uno", "esecutore", "ses_test")

    assert row["task"] == task
    assert row["unit"] == "unita-uno"
    assert row["role"] == "esecutore"
    assert row["harness"] == "opencode"
    assert row["session"] == "ses_test"
    assert row["source"] == "opencode session export"
    assert row["model"] == "opencode-go/deepseek-v4.1-flash#max"
    assert row["outcome"] == "succeeded"
    assert row["duration_s"] == 60.0
    assert row["tokens_in"] == 1000
    assert row["tokens_out"] == 200
    assert row["tokens_reasoning"] == 30
    assert row["tokens_cache_read"] == 400
    assert row["tokens_cache_write"] == 0
    assert row["cost_usd"] == 0.25
    assert row["stopped"] is True
    assert row["at"]

    lines = (lane / task / state.COSTS_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [row]


def test_a_running_session_is_not_reported_as_stopped(lane, task, export_returns):
    payload = json.loads(json.dumps(EXPORT))
    del payload["info"]["outcome"]
    del payload["info"]["time"]["idle"]
    export_returns(payload)

    row = costs.record(task, "unita-uno", "esecutore", "ses_test")

    assert row["outcome"] == ""
    assert row["stopped"] is False
    assert row["cost_usd"] == 0.25, "il costo già riportato resta un fatto anche a sessione aperta"


def test_an_export_that_fails_leaves_a_row_with_a_null_cost(lane, task, export_returns):
    export_returns("", code=1)

    row = costs.record(task, "unita-uno", "esecutore", "ses_test")

    assert row["cost_usd"] is None, "una fonte che non risponde non è un costo zero"
    assert "esce con 1" in row["note"]
    assert row["tokens_in"] is None
    assert row["model"] == ""
    assert (lane / task / state.COSTS_FILE).is_file(), "la riga si scrive comunque"


def test_a_missing_executable_leaves_a_row_with_a_null_cost(lane, task, monkeypatch):
    monkeypatch.setattr(costs.shutil, "which", lambda name: None)

    row = costs.record(task, "unita-uno", "esecutore", "ses_test")

    assert row["cost_usd"] is None
    assert "PATH" in row["note"]


def test_an_export_that_is_not_json_is_refused(lane, task, export_returns):
    export_returns("non sono JSON")

    row = costs.record(task, "unita-uno", "esecutore", "ses_test")

    assert row["cost_usd"] is None
    assert "non è JSON" in row["note"]


def test_the_export_goes_through_the_windows_shim_resolver(lane, task, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return costs.subprocess.CompletedProcess(argv, 0, json.dumps(EXPORT), "")

    monkeypatch.setattr(costs.subprocess, "run", fake_run)

    costs.record(task, "unita-uno", "esecutore", "ses_test")

    if os.name == "nt":
        # opencode su Windows è un .cmd: lanciato nudo non parte.
        assert seen[0][:4] == ["cmd.exe", "/d", "/s", "/c"]
        assert seen[0][4:] == [r"C:\fake\opencode.cmd", "session", "export", "ses_test"]
    else:
        assert seen[0] == [r"C:\fake\opencode.cmd", "session", "export", "ses_test"]


def test_an_empty_unit_is_refused(lane, task):
    with pytest.raises(state.SupervisionError, match="unità vuota"):
        costs.record(task, "  ", "esecutore", "ses_test")


def test_a_session_id_that_is_not_a_session_is_refused_before_any_process(lane, task, monkeypatch):
    """L'id finisce nella riga di comando di `opencode`: `&` avvierebbe un secondo comando."""
    launched: list[list[str]] = []

    def fake_run(argv, **kwargs):
        launched.append(list(argv))
        return costs.subprocess.CompletedProcess(argv, 0, json.dumps(EXPORT), "")

    monkeypatch.setattr(costs.subprocess, "run", fake_run)

    with pytest.raises(state.SupervisionError, match="sessione non valido"):
        costs.record(task, "unita-uno", "esecutore", "ses_x&calc")

    assert launched == [], "un id rifiutato non deve arrivare a nessun processo"
    assert not (lane / task / state.COSTS_FILE).exists(), "un rifiuto non lascia una riga di costo"


def test_export_refuses_a_session_id_it_would_splice_into_a_command(lane):
    with pytest.raises(costs.CostError, match="sessione non valido"):
        costs.export_session("ses_x&calc")


def test_an_unknown_task_is_refused(lane):
    with pytest.raises(state.SupervisionError, match="compito inesistente"):
        costs.record("20260101-000000-mai-creato", "unita-uno", "esecutore", "ses_test")


def test_the_aggregate_sums_by_role_and_by_unit(lane, task):
    rows = [
        {"cost_usd": 0.25, "tokens_in": 100, "tokens_out": 10, "role": "esecutore", "unit": "unita-uno"},
        {"cost_usd": None, "tokens_in": None, "tokens_out": None, "role": "esecutore", "unit": "unita-uno"},
        {"cost_usd": 0.5, "tokens_in": 300, "tokens_out": 30, "role": "scout", "unit": "unita-due"},
    ]
    for row in rows:
        state.append_jsonl(lane / task / state.COSTS_FILE, row)

    data = costs.aggregate(task)

    assert data["task"] == task
    assert data["rows"] == 3
    assert data["cost_usd"] == 0.75
    assert data["unknown_rows"] == 1
    assert data["tokens_in"] == 400
    assert data["tokens_out"] == 40
    assert data["by_role"]["esecutore"] == {
        "rows": 2, "cost_usd": 0.25, "unknown_rows": 1, "tokens_in": 100, "tokens_out": 10,
    }
    assert data["by_role"]["scout"]["cost_usd"] == 0.5
    assert data["by_unit"]["unita-uno"]["rows"] == 2
    assert data["by_unit"]["unita-due"]["cost_usd"] == 0.5


def test_an_empty_task_has_no_cost_total(lane, task):
    data = costs.aggregate(task)
    assert data["rows"] == 0
    assert data["cost_usd"] is None
    assert data["by_role"] == {}


def _fake_db(tmp_path, rows: list[tuple[str, str | None, float]]) -> Path:
    """Un archivio del harness in miniatura: solo la tabella che serve."""
    path = tmp_path / "opencode.db"
    con = sqlite3.connect(path)
    con.execute("create table session_v2 (id text primary key, parent_id text, cost real)")
    con.executemany("insert into session_v2 values (?, ?, ?)", rows)
    con.commit()
    con.close()
    return path


def test_the_children_of_a_session_are_counted_and_summed(tmp_path):
    db = _fake_db(tmp_path, [
        ("ses_padre", None, 0.5),
        ("ses_figlia", "ses_padre", 0.25),
        ("ses_nipote", "ses_figlia", 0.125),
        ("ses_altra", "ses_altro-padre", 9.0),
    ])

    count, total, source = costs.child_sessions("ses_padre", db=db)

    assert count == 2
    assert total == 0.375
    assert "opencode.db" in source


def test_a_missing_store_says_children_are_not_exposed(tmp_path):
    count, total, source = costs.child_sessions("ses_padre", db=tmp_path / "manca.db")

    assert count is None
    assert total is None
    assert "figlie non esposte" in source


def test_the_harness_store_is_found_through_the_engine_home(tmp_path, monkeypatch):
    """La casa la risolve il motore, non `Path.home()`: un NEXGEN_HOME sposta anche l'archivio."""
    store = tmp_path / ".local" / "share" / "opencode"
    store.mkdir(parents=True)
    (store / "opencode.db").write_text("", encoding="utf-8")
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    assert costs.opencode_db() == store / "opencode.db"


def test_a_store_without_a_session_table_says_so(tmp_path):
    path = tmp_path / "vuoto.db"
    con = sqlite3.connect(path)
    con.execute("create table altra_cosa (id text)")
    con.commit()
    con.close()

    count, total, source = costs.child_sessions("ses_padre", db=path)

    assert count is None
    assert "nessuna tabella delle sessioni" in source


def test_measure_adds_the_children_to_the_cost(lane, monkeypatch):
    monkeypatch.setattr(costs, "export_session", lambda session, **kwargs: EXPORT)
    monkeypatch.setattr(costs, "child_sessions", lambda session, **kwargs: (2, 0.5, "figlie da opencode.db"))

    facts = costs.measure("ses_test")

    assert facts.cost_usd == 0.75, "0.25 della sessione più 0.5 delle due figlie"
    assert facts.children == 2
    assert facts.children_cost_usd == 0.5


def test_the_row_carries_the_children_and_the_source(lane, task, monkeypatch):
    monkeypatch.setattr(costs, "export_session", lambda session, **kwargs: EXPORT)
    monkeypatch.setattr(costs, "child_sessions", lambda session, **kwargs: (1, 0.5, "figlie da opencode.db"))

    row = costs.record(task, "unita-uno", "esecutore", "ses_test")

    assert row["cost_usd"] == 0.75
    assert row["children"] == 1
    assert row["children_cost_usd"] == 0.5
    assert row["children_source"] == "figlie da opencode.db"


def test_a_source_that_cannot_read_children_still_writes_the_parent_cost(lane, task, monkeypatch):
    monkeypatch.setattr(costs, "export_session", lambda session, **kwargs: EXPORT)
    monkeypatch.setattr(costs, "child_sessions", lambda session, **kwargs: (None, None, "figlie non esposte: archivio non trovato"))

    row = costs.record(task, "unita-uno", "esecutore", "ses_test")

    assert row["cost_usd"] == 0.25, "senza figlie leggibili il costo noto resta quello della sessione"
    assert row["children"] is None
    assert "figlie non esposte" in row["children_source"]


def test_an_export_that_says_not_found_is_a_missing_session(lane, monkeypatch):
    monkeypatch.setattr(
        costs.subprocess, "run",
        lambda argv, **kwargs: costs.subprocess.CompletedProcess(argv, 1, "", "Error: Session not found"),
    )

    with pytest.raises(costs.SessionMissing, match="non trova la sessione"):
        costs.export_session("ses_sparita")


def test_a_timed_out_export_is_not_a_missing_session(lane, monkeypatch):
    """Un timeout non dice che la sessione non esiste: è un errore transitorio."""
    def timeout(*args, **kwargs):
        raise costs.subprocess.TimeoutExpired("opencode", 1)

    monkeypatch.setattr(costs.subprocess, "run", timeout)

    with pytest.raises(costs.CostError) as caught:
        costs.export_session("ses_viva")

    assert not isinstance(caught.value, costs.SessionMissing)

# --- il costo di una raccolta: giro, unità, compito --------------------------


def _facts(*, cost_usd=2.6, model="opencode-go/x", stopped=True):
    """La sessione come la riporta il harness: ferma vuol dire idle dopo l'ultimo aggiornamento."""
    return costs.SessionFacts(
        model=model, cost_usd=cost_usd, created_ms=1_000, updated_ms=2_000,
        idle_ms=2_000 if stopped else 0,
    )


def test_a_collection_prices_the_round_the_unit_and_the_task(lane):
    task = state.start("prova costi di raccolta")["id"]
    state.append_event(task, "unit_started", unit="prova", harness="opencode", session="ses_uno", mode="run")
    events = state.read_events(task)
    started = events[-1]["at"]
    rows = [
        {"at": "2000-01-01T00:00:00+00:00", "unit": "prova", "session": "ses_uno", "cost_usd": 1.0},
        {"at": started, "unit": "prova", "session": "ses_uno", "cost_usd": 2.0},
    ]

    price = costs.collect_cost(task, "prova", facts_of=lambda session: _facts(), rows=rows, events=events)

    assert price.round_usd == 0.6, "dal costo scritto quando il giro è partito"
    assert price.unit_usd == 1.6, "dall'aggancio dell'unità"
    assert price.task_usd == 2.6, "la sessione intera, figlie comprese"
    assert price.model == "opencode-go/x"
    assert price.stopped is True
    assert price.known is True


def test_the_task_total_adds_the_sessions_that_ended_before(lane):
    task = state.start("prova due sessioni")["id"]
    rows = [
        {"unit": "prima", "session": "ses_vecchia", "cost_usd": 0.5},
        {"unit": "prima", "session": "ses_vecchia", "cost_usd": 0.9},
        {"unit": "prova", "session": "ses_uno", "cost_usd": 1.0},
    ]

    price = costs.collect_cost(task, "prova", facts_of=lambda session: _facts(cost_usd=2.6), rows=rows, events=[])

    assert price.task_usd == 3.5, "l'ultimo numero della sessione vecchia, più quella viva"


def test_a_task_total_with_an_unreadable_session_stays_unknown(lane):
    task = state.start("prova sessione senza costo")["id"]
    rows = [
        {"unit": "prima", "session": "ses_vecchia", "cost_usd": None},
        {"unit": "prova", "session": "ses_uno", "cost_usd": 1.0},
    ]

    price = costs.collect_cost(task, "prova", facts_of=lambda session: _facts(cost_usd=2.6), rows=rows, events=[])

    assert price.task_usd is None, "un numero che manca non diventa zero"
    assert price.known is False


def test_a_session_the_harness_cannot_price_is_not_zero(lane):
    task = state.start("prova prezzo illeggibile")["id"]
    state.append_event(task, "unit_started", unit="prova", harness="opencode", session="ses_uno", mode="run")

    def broken(session):
        raise costs.CostError("l'export non risponde")

    price = costs.collect_cost(task, "prova", facts_of=broken)

    assert price.known is False
    assert "l'export non risponde" in price.note
    assert price.round_usd is None and price.unit_usd is None and price.task_usd is None


def test_a_task_without_a_session_says_so(lane):
    task = state.start("prova senza sessione")["id"]

    price = costs.collect_cost(task, "prova", facts_of=lambda session: _facts())

    assert price.known is False
    assert "nessuna sessione" in price.note


def test_the_line_is_the_same_sentence_everywhere():
    price = costs.CollectCost(
        round_usd=0.125, unit_usd=0.5, task_usd=0.75, model="opencode-go/x", session="ses_uno",
    )

    assert costs.cost_text(price) == "costo: giro 0.1250 $, unità 0.5000 $, compito 0.7500 $ (modello opencode-go/x)"


def test_the_line_of_an_unreadable_cost_names_the_reason():
    price = costs.CollectCost(note="l'export non risponde")

    assert costs.cost_text(price) == "costo: non misurato (l'export non risponde)"
