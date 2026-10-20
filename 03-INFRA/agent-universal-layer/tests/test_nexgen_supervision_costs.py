"""I costi di una sessione harness: export finto, riga attesa, aggregazione.

Nessun test chiama `opencode`: l'export è un JSON letterale restituito da un
`subprocess.run` finto, e l'eseguibile è un finto `which`.
"""
from __future__ import annotations

import json
import os

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
