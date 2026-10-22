"""Pulizia: copie e rami di unità chiuse, e le cartelle che il servizio tiene dopo la copia.

Tabelle di processi e servizi finti: nessun test avvia OpenCode, uccide processi
veri o legge la macchina. Ciò che si prova è che il motore elenca solo ciò che è
chiuso, dice l'ora di avvio quando la sa, e non inventa una riga quando non sa.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from nexgen_local import supervision_adapters as adapters
from nexgen_local import supervision_cleanup as cleanup
from nexgen_local import supervision_state as state


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def task(lane):
    return state.start("prova pulizia fase 3c")["id"]


def _unit(task: str, tmp_path, name: str = "prova", *, published: bool = False) -> dict:
    copy = tmp_path / f"copia-{name}"
    copy.mkdir()
    record = {
        "unit": name, "worktree": str(copy), "branch": f"sup/{task}/{name}",
        "base": "0" * 40, "allow": [], "verify": "", "repo": str(tmp_path / "repo"),
    }
    state.append_event(task, "unit_created", **record)
    if published:
        state.append_event(
            task, "unit_published", unit=name, commits=["a" * 40], head="a" * 40,
            branch=record["branch"], repo=record["repo"], pushed=False,
        )
    return record


def _row(folder: str, created: float = 1000.0, pid: int = 41) -> dict:
    return {
        "pid": pid, "ppid": 1, "created": created, "name": "opencode",
        "command_line": f"opencode serve {folder}",
    }


def test_a_unit_still_being_worked_in_is_not_a_leftover(task, tmp_path):
    _unit(task, tmp_path)

    assert cleanup.leftovers() == []


def test_an_open_unit_is_not_listed_even_when_its_branch_is_gone(task, tmp_path):
    _unit(task, tmp_path, published=False)

    found = cleanup.survey(table=[])

    assert found["leftovers"] == []


def test_survey_lists_a_closed_units_copy_and_branch(task, tmp_path):
    _unit(task, tmp_path, published=True)

    found = cleanup.survey(table=[])

    assert [row["unit"] for row in found["leftovers"]] == ["prova"]
    assert found["leftovers"][0]["copy"] == str(tmp_path / "copia-prova")
    printed = cleanup.lines(found)
    assert any("copie e rami di unità finite ancora sul disco: 1" in line for line in printed)


def test_close_leftovers_refuses_when_the_published_repository_is_gone(task, tmp_path):
    _unit(task, tmp_path, published=True)

    result = cleanup.close_leftovers(cleanup.leftovers())

    assert result["closed"] == []
    assert len(result["problems"]) == 1
    assert "il diario non dice su quale ramo di quale repository" in result["problems"][0]
    assert (tmp_path / "copia-prova").is_dir(), "una chiusura rifiutata non cancella niente"


def test_deep_survey_lists_the_folders_the_server_holds_after_their_copy_went(monkeypatch, tmp_path):
    gone = str(tmp_path / "sparita")
    alive = str(tmp_path / "viva")
    (tmp_path / "viva").mkdir()
    monkeypatch.setattr(adapters.get("opencode"), "locations", lambda: [gone, alive])

    found = cleanup.survey(table=[_row(gone, created=1_700_000_000.0)], deep=True)

    assert [row["directory"] for row in found["locations"]] == [gone]
    assert found["locations"][0]["started"] == datetime.fromtimestamp(1_700_000_000.0).isoformat(
        timespec="seconds"
    )
    assert any("server MCP di una copia chiusa: 1" in line for line in cleanup.leftover_lines(found))


def test_the_shallow_survey_does_not_start_the_harness_cli(monkeypatch):
    def refuse():
        raise AssertionError("la ricognizione superficiale non deve interrogare il servizio")

    monkeypatch.setattr(adapters.get("opencode"), "locations", refuse)

    assert cleanup.survey(table=[])["locations"] == []


def test_orphan_locations_says_nothing_when_the_server_cannot_be_read(monkeypatch):
    def broken():
        raise adapters.base.AdapterError("servizio non raggiungibile")

    monkeypatch.setattr(adapters.get("opencode"), "locations", broken)

    assert cleanup.orphan_locations(table=[]) == []


def test_orphan_locations_without_a_process_start_time_leaves_it_unsaid():
    found = cleanup.orphan_locations(
        lister=lambda: ["C:/sparita"], exists=lambda value: False, table=[],
    )

    assert found == [{"directory": "C:/sparita", "started": ""}]
    printed = cleanup.leftover_lines({"leftovers": [], "locations": found})
    assert any("ora di avvio non esposta dal server" in line for line in printed)


def test_evict_locations_names_what_went_and_what_did_not(monkeypatch):
    adapter = adapters.get("opencode")
    monkeypatch.setattr(
        adapter, "dispose_location",
        lambda folder: (not str(folder).endswith("viva"), f"risposta del servizio per {folder}"),
    )

    lines = cleanup.evict_locations([{"directory": "C:/morta"}, {"directory": "C:/viva"}])

    assert lines[0].startswith("smaltita C:/morta:")
    assert lines[1].startswith("non smaltita C:/viva:")
    assert cleanup.evict_locations([]) == []
