"""La pulizia: le schede dei compiti finiti o spariti, i processi senza marcatore, il servizio.

Tabelle di processi finte e una corsia temporanea: nessun test chiude una scheda
vera né legge la tabella dei processi della macchina.
"""
from __future__ import annotations

import pytest

from nexgen_local import cli
from nexgen_local import supervision_cleanup as cleanup
from nexgen_local import supervision_state as state
from nexgen_local import supervision_windows as windows


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def posix(pretend_os):
    """Le righe finte sono nella forma che Linux legge: il marcatore nell'ambiente."""
    pretend_os(windows, "posix")


def _task(lane, objective: str = "prova pulizia") -> str:
    return state.start(objective)["id"]


def _unit(task: str, name: str = "prova", *, approved: bool = False) -> None:
    state.append_event(
        task, "unit_created", unit=name, worktree="C:/copia", branch="sup/x",
        base="0" * 40, allow=[], verify="", repo="C:/copia",
    )
    if approved:
        state.append_event(
            task, "unit_reviewed", unit=name, round=1, verdict=state.APPROVED, head="a" * 40,
        )


def _tab(task: str, role: str = "esecutore", pid: int = 100, ppid: int = 1) -> dict:
    """The shell of one tab, with the marker the engine exported."""
    marker = windows.tab_marker(task, role)
    return {
        "pid": pid, "ppid": ppid, "name": "bash", "command_line": f"bash -c ... {marker} ...",
        "environ": f"HOME=/tmp/casa\0{windows.TAB_ENV}={marker}\0",
    }


def _child(pid: int = 200, ppid: int = 100, name: str = "opencode", command: str = "opencode /copia") -> dict:
    return {"pid": pid, "ppid": ppid, "name": name, "command_line": command, "environ": ""}


# --- cosa è vecchio e cosa no -------------------------------------------------


def test_a_finished_task_leaves_a_stale_tab_with_its_whole_tree(lane, posix):
    task = _task(lane)
    _unit(task, approved=True)

    found = cleanup.survey(table=[_tab(task), _child()])

    assert [tab.reason for tab in found["close"]] == ["finito"]
    assert found["close"][0].pids == (200, 100), "dal figlio alla shell"
    assert found["keep"] == []


def test_a_task_the_lane_cannot_open_any_more_is_stale(lane, posix):
    """Una prova in una NEXGEN_HOME temporanea sparisce dalla corsia: la scheda no."""
    found = cleanup.survey(table=[_tab("20200101-000000-mai-esistito")])

    assert [tab.reason for tab in found["close"]] == ["sconosciuto"]


def test_a_task_still_working_keeps_its_tab(lane, posix):
    task = _task(lane)
    _unit(task)

    found = cleanup.survey(table=[_tab(task)])

    assert found["close"] == []
    assert [tab.reason for tab in found["keep"]] == ["aperto"]


def test_a_scout_tab_stays_while_its_round_is_open(lane, posix):
    task = _task(lane)
    _unit(task, approved=True)
    state.append_event(
        task, "scout_opened", round=1, folder="C:/ricognizione", base="main",
        head="a" * 40, brief="b", report="r", window="w", trusted=True,
    )

    found = cleanup.survey(table=[_tab(task, "scout")])

    assert found["keep"][0].reason == "aperto"


def test_a_scout_tab_without_units_stays_while_its_round_is_open(lane, posix):
    """La ricognizione parte prima della prima unità: non è una scheda da chiudere."""
    task = _task(lane)
    state.append_event(
        task, "scout_opened", round=1, folder="C:/ricognizione", base="main",
        head="a" * 40, brief="b", report="r", window="w", trusted=True,
    )

    found = cleanup.survey(table=[_tab(task, "scout")])

    assert found["close"] == [], "lo scout sta lavorando: la pulizia non lo uccide"
    assert [tab.reason for tab in found["keep"]] == ["aperto"]


def test_a_scout_tab_of_a_closed_round_is_stale(lane, posix):
    task = _task(lane)
    _unit(task, approved=True)
    state.append_event(task, "scout_opened", round=1, folder="C:/r", base="main", head="a" * 40)
    state.append_event(task, "scout_finished", round=1, folder="C:/r", clean=True)

    found = cleanup.survey(table=[_tab(task, "scout")])

    assert found["close"][0].reason == "finito"


# --- la chiusura -------------------------------------------------------------


def test_closing_kills_deepest_first_and_says_why_in_the_journal(lane, posix):
    task = _task(lane)
    _unit(task, approved=True)
    found = cleanup.survey(table=[_tab(task), _child()])
    killed: list = []

    result = cleanup.close_tabs(found["close"], killer=killed.extend)

    assert killed == [200, 100]
    assert result["closed"][0].pids == (200, 100)
    events = [row for row in state.read_events(task) if row.get("event") == "tab_closed"]
    assert events and events[0]["why"] == "pulizia"


def test_closing_a_gone_task_does_not_look_for_its_folder(lane, posix):
    found = cleanup.survey(table=[_tab("20200101-000000-mai-esistito")])
    killed: list = []

    cleanup.close_tabs(found["close"], killer=killed.extend)

    assert killed == [100]


def test_a_stale_tab_that_holds_the_shared_server_is_kept(lane, posix):
    task = _task(lane)
    _unit(task, approved=True)
    rows = [_tab(task), _child(200, 100, "opencode", "opencode serve --service")]
    found = cleanup.survey(table=rows)

    assert found["close"] == [], "un albero con il servizio condiviso non è chiudibile"
    assert found["keep"][0].shared == 200
    assert any("servizio condiviso 200" in line for line in cleanup.lines(found))


def test_closing_refuses_a_tree_with_the_shared_server(lane, posix):
    """La cintura di `close_tabs`, provata direttamente: anche se gliela si passa, non la chiude."""
    task = _task(lane)
    _unit(task, approved=True)
    tab = cleanup.survey(
        table=[_tab(task), _child(200, 100, "opencode", "opencode serve --service")],
    )["keep"][0]
    killed: list = []

    result = cleanup.close_tabs([tab], killer=killed.extend)

    assert killed == []
    assert result["kept"] == [tab]
    assert result["closed"] == []


# --- quello che resta senza scheda -------------------------------------------


def test_workers_without_a_tab_are_listed_and_tab_workers_are_not(lane, posix):
    task = _task(lane)
    _unit(task, approved=True)
    rows = [
        _tab(task), _child(),
        _child(900, 1, "opencode.exe", "opencode C:/altro"),
        _child(901, 1, "agy.exe", "agy --mode plan"),
        _child(902, 1, "python.exe", "python -m http.server"),
    ]

    found = cleanup.survey(table=rows)

    assert [row["pid"] for row in found["unmarked"]] == [900, 901]


def test_the_shared_server_is_counted_with_what_hangs_under_it(lane, posix):
    rows = [
        _child(500, 1, "opencode", "opencode serve --service"),
        _child(501, 500, "python.exe", "python -m mcp"),
        _child(502, 501, "python.exe", "python -m mcp"),
    ]

    found = cleanup.survey(table=rows)

    assert found["server"] == [{"pid": 500, "children": 2}]


def test_the_lines_say_when_the_service_can_be_restarted(lane, posix):
    rows = [_child(500, 1, "opencode", "opencode serve --service"), _child(501, 500, "python.exe", "python -m mcp")]

    quiet = cleanup.lines(cleanup.survey(table=rows))

    assert any("nessuna sessione attiva: si può riavviare il servizio" in line for line in quiet)


def test_the_lines_do_not_invite_a_restart_while_a_session_works(lane, posix):
    task = _task(lane)
    _unit(task)
    rows = [_tab(task), _child(500, 1, "opencode", "opencode serve --service")]

    busy = cleanup.lines(cleanup.survey(table=rows))

    assert any("non riavviare il servizio" in line for line in busy)


def test_the_lines_do_not_invite_a_restart_while_a_worker_without_a_tab_runs(lane, posix):
    """Anche una sessione senza scheda, come un `opencode run`, morirebbe col servizio."""
    rows = [
        _child(500, 1, "opencode", "opencode serve --service"),
        _child(900, 1, "opencode.exe", "opencode run -s ses_ea2b01bd6fprova leggi il brief"),
    ]

    busy = cleanup.lines(cleanup.survey(table=rows))

    assert any("non riavviare il servizio" in line for line in busy)
    assert not any("si può riavviare" in line for line in busy)


# --- la riga informativa di `sup collect` e il verbo -------------------------


def test_collect_closes_a_stopped_tab_before_the_report_is_read(monkeypatch):
    """Codex su Windows tiene il resoconto illeggibile finché la scheda è viva."""
    from nexgen_local import supervision_costs as costs
    from nexgen_local.cmds import sup as sup_cmd

    monkeypatch.setattr(
        sup_cmd.state, "unit_record",
        lambda task, unit: {"unit": "prova", "harness": "codex"},
    )
    monkeypatch.setattr(
        sup_cmd.costs, "collect_cost",
        lambda task, unit, harness: costs.CollectCost(stopped=True),
    )
    calls = []
    monkeypatch.setattr(
        sup_cmd.windows, "close_tab",
        lambda task, role, **kw: calls.append((task, role, kw)) or {"line": "scheda chiusa"},
    )

    line = sup_cmd._close_stopped_tab("t1", "prova")

    assert line == "scheda chiusa"
    assert calls == [("t1", "esecutore", {"still_working": ("prova",), "stopped": True})]


def test_collect_leaves_a_working_tab_alone(monkeypatch):
    from nexgen_local import supervision_costs as costs
    from nexgen_local.cmds import sup as sup_cmd

    monkeypatch.setattr(
        sup_cmd.state, "unit_record",
        lambda task, unit: {"unit": "prova", "harness": "codex"},
    )
    monkeypatch.setattr(
        sup_cmd.costs, "collect_cost",
        lambda task, unit, harness: costs.CollectCost(stopped=False),
    )
    calls = []
    monkeypatch.setattr(
        sup_cmd.windows, "close_tab",
        lambda *a, **kw: calls.append(a) or {"line": "non doveva chiudere"},
    )

    assert sup_cmd._close_stopped_tab("t1", "prova") == ""
    assert calls == []


def test_the_collect_note_counts_the_tabs_of_gone_tasks(lane, posix, monkeypatch):
    rows = [_tab("20200101-000000-mai-esistito"), _tab("20200102-000000-mai-esistito", pid=101)]
    monkeypatch.setattr(cleanup.processes, "rows", lambda: rows)

    notes = cleanup.notes()

    assert len(notes) == 1
    assert "2" in notes[0] and "sup pulizia" in notes[0]


def test_no_note_when_every_tab_belongs_to_a_task_that_is_still_there(lane, posix, monkeypatch):
    task = _task(lane)
    _unit(task)
    monkeypatch.setattr(cleanup.processes, "rows", lambda: [_tab(task)])

    assert cleanup.notes() == []


def test_the_verb_lists_and_only_closes_when_asked(lane, posix, capsys, monkeypatch):
    task = _task(lane)
    _unit(task, approved=True)
    monkeypatch.setattr(cleanup.processes, "rows", lambda: [_tab(task)])
    killed: list = []
    monkeypatch.setattr(cleanup.processes, "kill", lambda pids: killed.extend(pids))
    capsys.readouterr()

    assert cli.main(["sup", "pulizia"]) == 0
    out = capsys.readouterr().out
    assert "schede da chiudere: 1" in out
    assert killed == [], "senza --chiudi non si tocca niente"

    assert cli.main(["sup", "pulizia", "--chiudi"]) == 0
    out = capsys.readouterr().out
    assert killed == [100]
    assert "chiuse: 1 schede (1 processi)" in out
