"""Una scheda per unità: la vecchia si chiude, e la scheda di un'unità finita non conta.

Il supervisore ha trovato nove TUI aperte da ore, una per unità e alcune di unità già
pubblicate; con una scheda dimenticata in giro la regola "nati prima della sessione
attiva più vecchia" non raccoglieva più niente. Qui si prova, con tabelle finte e
registratori, che una unità ha una scheda sola, che quella di un'unità pubblicata o
ritirata si chiude e non è una sessione attiva, e che una TUI senza marcatore invece
non si tocca e lo dice.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_local import supervision_cleanup as cleanup
from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_retire as retire
from nexgen_local import supervision_service as service
from nexgen_local import supervision_state as state
from nexgen_local import supervision_windows as windows

SERVER_PID = 900
UNIT = "prova"


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def task(lane):
    return state.start("prova schede")["id"]


@pytest.fixture
def posix(pretend_os):
    """Le righe finte sono nella forma che Linux legge: il marcatore nell'ambiente."""
    pretend_os(windows, "posix")


@pytest.fixture
def copy(task, lane) -> Path:
    folder = lane / task / f"23-unita-{UNIT}"
    folder.mkdir(parents=True)
    (lane / task / f"20-brief-{UNIT}.md").write_text("# Brief\n", encoding="utf-8")
    state.append_event(
        task, "unit_created", unit=UNIT, worktree=str(folder), branch=f"sup/{task}/{UNIT}",
        base="0" * 40, allow=["docs/*.md"], verify="", repo=str(folder),
    )
    return folder


def _published(task: str) -> None:
    state.append_event(
        task, "unit_published", unit=UNIT, commits=["a" * 40], head="a" * 40,
        branch=f"sup/{task}/{UNIT}", repo="C:/repo", pushed=True,
    )


def _tab(task: str, *, pid: int = 100, unit: str = UNIT, created: float = 1000.0) -> dict:
    """La shell di una scheda del motore, con il marcatore e la cartella dell'unità."""
    marker = windows.tab_marker(task, "esecutore")
    return {
        "pid": pid, "ppid": 1, "created": created, "name": "bash",
        "command_line": f"bash -c opencode {unit}/23-unita-{unit} --prompt ...",
        "environ": f"HOME=/tmp/casa\0{windows.TAB_ENV}={marker}\0",
    }


def _tui(pid: int = 200, ppid: int = 100, created: float = 1001.0) -> dict:
    return {
        "pid": pid, "ppid": ppid, "created": created, "name": "opencode",
        "command_line": "opencode -s ses_prova", "environ": "",
    }


def _child(pid: int = 300, created: float = 1200.0) -> dict:
    return {
        "pid": pid, "ppid": SERVER_PID, "created": created, "name": "python",
        "command_line": "python un-mcp.py", "environ": "",
    }


def _server(created: float = 500.0) -> dict:
    return {
        "pid": SERVER_PID, "ppid": 1, "created": created, "name": "opencode",
        "command_line": "opencode serve --port 4096", "environ": "",
    }


class Killer:
    """Al posto di `processes.kill`: registra i pid, non ne ferma nessuno."""

    def __init__(self) -> None:
        self.stopped: list[int] = []

    def __call__(self, pids: list[int]) -> None:
        self.stopped.extend(pids)


# --- una scheda sola per unità ------------------------------------------------


def test_a_second_run_closes_the_tab_the_unit_already_had(task, copy, monkeypatch):
    state.append_event(
        task, "unit_started", unit=UNIT, harness="opencode", mode="tab", folder=str(copy),
    )
    replaced: list[tuple] = []
    monkeypatch.setattr(harness, "_close_unit_tab", lambda task_id, unit: (
        replaced.append((task_id, unit)) or {"line": f"scheda esecutore di {unit} chiusa (3 processi)"}
    ))
    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    sessions = [{"id": "ses_nuova", "directory": str(copy), "created": 10**13}]

    result = harness.run_unit(
        task, UNIT, opener=lambda title, argv, *, cwd, marker="": {"title": title, "pid": 4242},
        lister=lambda: sessions, wait_s=0,
    )

    assert replaced == [(task, UNIT)], "la scheda vecchia si chiude prima della nuova"
    assert "chiusa (3 processi)" in result["replaced_tab"]["line"]


def test_a_unit_with_no_tab_in_the_journal_costs_no_process_table_read(task, copy, monkeypatch):
    def refuse(task_id, unit):
        raise AssertionError("nessuna scheda registrata: non si cerca nel processo")

    monkeypatch.setattr(harness, "_close_unit_tab", refuse)
    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    sessions = [{"id": "ses_nuova", "directory": str(copy), "created": 10**13}]

    result = harness.run_unit(
        task, UNIT, opener=lambda title, argv, *, cwd, marker="": {"title": title, "pid": 4242},
        lister=lambda: sessions, wait_s=0,
    )

    assert "replaced_tab" not in result


def test_a_closed_tab_is_not_looked_for_again(task, copy):
    state.append_event(task, "unit_started", unit=UNIT, mode="tab", folder=str(copy))
    state.append_event(task, "tab_closed", role="esecutore", unit=UNIT, pids=[100])

    assert windows.unit_tab_open(task, UNIT) is False


def test_the_retirement_closes_the_units_own_tab(task, copy, tmp_path, monkeypatch):
    state.append_event(task, "unit_started", unit=UNIT, mode="tab", folder=str(copy))
    closed: list[tuple] = []
    monkeypatch.setattr(
        windows, "close_unit_tab",
        lambda task_id, unit: closed.append((task_id, unit)) or {"line": f"scheda esecutore di {unit} chiusa (2 processi)"},
    )
    record = {
        "unit": UNIT, "worktree": str(copy), "branch": f"sup/{task}/{UNIT}",
        "repo": str(tmp_path / "repo"), "published": ["a" * 40],
    }
    monkeypatch.setattr(retire.state, "unit_record", lambda task_id, unit: record)
    monkeypatch.setattr(retire, "_keep_history", lambda *a, **k: {"ref": "", "head": "a" * 40})
    monkeypatch.setattr(retire, "_release_local", lambda *a, **k: [])
    monkeypatch.setattr(retire, "_remove_copy", lambda *a, **k: None)
    monkeypatch.setattr(retire, "_delete_branch", lambda *a, **k: None)

    result = retire.retire(task, UNIT, why="prova", commits=["a" * 40])

    assert closed == [(task, UNIT)]
    assert any("chiusa (2 processi)" in note for note in result["notes"])


# --- la scheda di un'unità finita non è una sessione --------------------------


def test_the_tab_of_a_published_unit_is_not_an_active_session(task, copy, posix):
    _published(task)
    rows = [_server(), _tab(task), _tui(), _child()]

    before, targets = service.plan(rows)

    assert before is None, "la scheda di un'unità pubblicata non protegge niente"
    assert {row["pid"] for row in targets} == {300}


def test_the_tab_of_a_retired_unit_is_not_an_active_session(task, copy, posix):
    state.append_event(task, "unit_retired", unit=UNIT, why="prova", copy=str(copy), branch="sup/x")
    rows = [_server(), _tab(task), _child()]

    before, _targets = service.plan(rows)

    assert before is None


def test_the_tab_of_an_open_unit_is_still_a_session(task, copy, posix):
    rows = [_server(), _tab(task), _tui(), _child()]

    before, targets = service.plan(rows)

    assert before == 1000.0
    assert targets == []


def test_the_tab_of_a_published_unit_is_listed_and_closed(task, copy, posix):
    _published(task)
    rows = [_server(), _tab(task), _tui(), _child()]

    items = cleanup.finished_unit_tabs(table=rows)
    killer = Killer()
    result = cleanup.close_finished_tabs(items, killer=killer)

    assert [(item["task"], item["unit"]) for item in items] == [(task, UNIT)]
    assert set(killer.stopped) == {100, 200}, "la shell e la TUI che ha dentro"
    assert [item["unit"] for item in result["closed"]] == [UNIT]
    assert not [row for row in state.read_events(task) if row.get("event") == "tab_left_open"]


def test_the_lines_say_which_unit_tab_is_still_open(task, copy, posix):
    _published(task)

    lines = cleanup.lines(cleanup.survey(table=[_server(), _tab(task)]))

    assert "schede di unità pubblicate o ritirate ancora aperte: 1" in lines
    assert any(UNIT in line for line in lines)


def test_a_tab_that_holds_the_shared_server_is_left_open(task, copy, posix):
    _published(task)
    shell = _tab(task)
    inner_server = {
        "pid": SERVER_PID, "ppid": shell["pid"], "created": 1001.0, "name": "opencode",
        "command_line": "opencode serve --port 4096", "environ": "",
    }
    killer = Killer()

    result = cleanup.close_finished_tabs(
        cleanup.finished_unit_tabs(table=[shell, inner_server]), killer=killer,
    )

    assert killer.stopped == []
    assert result["kept"] and result["kept"][0]["shared"] == SERVER_PID


def test_a_scout_tab_is_not_a_finished_unit_tab(task, copy, posix):
    """Una scheda scout segue il suo giro: un giro aperto è una sessione viva anche a unità chiuse."""
    _published(task)
    marker = windows.tab_marker(task, "scout")
    scout = {
        "pid": 500, "ppid": 1, "created": 1000.0, "name": "bash",
        "command_line": "bash -c opencode run --agent plan ...", "environ": f"{windows.TAB_ENV}={marker}\0",
    }

    assert cleanup.finished_unit_tabs(table=[scout]) == []
    before, _targets = service.plan([_server(), scout])
    assert before == 1000.0, "resta una sessione attiva"


# --- la TUI senza marcatore --------------------------------------------------


def test_a_session_without_a_marker_is_never_touched_and_blocks_the_collection(posix):
    rows = [
        _server(),
        # una TUI del proprietario, nata fuori dal motore: nessun marcatore
        {"pid": 700, "ppid": 1, "created": 1000.0, "name": "opencode",
         "command_line": "opencode -s ses_sua", "environ": ""},
        _child(created=1200.0),
        _child(pid=301, created=600.0),
    ]

    before, targets = service.plan(rows)
    lines = service.lines(service.survey(rows))

    assert before == 1000.0
    assert {row["pid"] for row in targets} == {301}, "solo ciò che è nato prima, e non la sessione"
    assert any("senza marcatore" in line and "blocca la raccolta" in line for line in lines)
