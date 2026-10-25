"""Il server condiviso e i figli vecchi: chi si raccoglie e chi non si tocca mai.

Tabelle di processi finte e un registratore al posto del killer: nessun test legge la
tabella della macchina, ferma un processo vero o parla al servizio. La regola provata
è quella che il proprietario ha applicato a mano: figli diretti del server nati prima
della sessione attiva più vecchia, con tutto il loro albero; senza sessioni attive
tutti; mai il server, mai un processo fuori dal suo albero.
"""
from __future__ import annotations

import pytest

from nexgen_local import supervision_processes as processes
from nexgen_local import supervision_service as service

SERVER_PID = 900
OLD = 600.0
MIDDLE = 1100.0
SESSION_START = 1000.0
NEW = 1200.0


def _server(created: float = 500.0) -> dict:
    return {
        "pid": SERVER_PID, "ppid": 1, "name": "opencode.exe", "created": created,
        "command_line": "opencode serve --port 4096", "environ": "",
    }


def _child(pid: int, created: float, *, ppid: int = SERVER_PID, name: str = "python.exe") -> dict:
    return {
        "pid": pid, "ppid": ppid, "name": name, "created": created,
        "command_line": f"{name} un-mcp.py", "environ": "",
    }


def _session(pid: int = 4242, created: float = SESSION_START, name: str = "opencode.exe") -> dict:
    return {
        "pid": pid, "ppid": 1, "name": name, "created": created,
        "command_line": "opencode -s ses_prova", "environ": "",
    }


class Killer:
    """Al posto di `processes.kill`: registra i pid, non ne ferma nessuno."""

    def __init__(self, fail=()) -> None:
        self.stopped: list[int] = []
        self.fail = fail

    def __call__(self, pids: list[int]) -> None:
        self.stopped.extend(pids)
        if any(pid in self.fail for pid in pids):
            raise processes.ProcessError("permesso negato a fermare un processo")


def _table_with_an_old_and_a_new_child() -> list[dict]:
    """Un figlio vecchio con il suo albero, uno nuovo, una sessione viva e un estraneo."""
    return [
        _server(),
        _child(11, OLD),
        _child(12, OLD + 1, ppid=11, name="conhost.exe"),
        _child(13, MIDDLE, name="node.exe"),
        _child(21, NEW),
        _session(),
        {"pid": 31, "ppid": 1, "name": "explorer.exe", "created": 10.0, "command_line": "", "environ": ""},
    ]


# --- cosa è vecchio e cosa è nuovo -------------------------------------------


def test_a_child_born_before_the_oldest_session_goes_with_its_whole_tree():
    before, targets = service.plan(_table_with_an_old_and_a_new_child())

    assert before == SESSION_START
    assert {row["pid"] for row in targets} == {11, 12}


def test_a_child_born_after_the_oldest_session_stays():
    _, targets = service.plan(_table_with_an_old_and_a_new_child())

    assert 21 not in {row["pid"] for row in targets}


def test_deepest_first_so_a_parent_never_survives_its_child():
    _, targets = service.plan(_table_with_an_old_and_a_new_child())

    assert [row["pid"] for row in targets] == [12, 11]


def test_without_a_live_session_every_direct_child_goes():
    table = [_server(), _child(11, OLD), _child(21, NEW)]

    before, targets = service.plan(table)

    assert before is None
    assert {row["pid"] for row in targets} == {11, 21}


def test_the_server_itself_is_never_stopped():
    _, targets = service.plan(_table_with_an_old_and_a_new_child())

    assert SERVER_PID not in {row["pid"] for row in targets}


def test_a_process_outside_the_servers_tree_is_never_touched():
    table = [
        _server(), _session(),
        {"pid": 77, "ppid": 1, "name": "node.exe", "created": 20.0, "command_line": "node x.js", "environ": ""},
    ]

    _, targets = service.plan(table)

    assert 77 not in {row["pid"] for row in targets}


def test_a_child_with_no_birth_time_is_left_while_a_session_lives():
    table = [_server(), _session(), {**_child(11, 0.0), "created": None}]
    table[-1].pop("created")

    _, targets = service.plan(table)

    assert 11 not in {row["pid"] for row in targets}


def test_a_grandchild_born_after_the_session_goes_with_its_old_parent():
    table = [_server(), _session(), _child(11, OLD), _child(12, NEW + 50, ppid=11)]

    _, targets = service.plan(table)

    assert {row["pid"] for row in targets} == {11, 12}


def test_a_session_running_inside_node_counts_as_a_live_session():
    """Un OpenCode installato come script gira dentro node: senza riconoscerlo si raccoglierebbe tutto."""
    table = [
        _server(), _child(21, NEW),
        {"pid": 51, "ppid": 1, "name": "node.exe", "created": SESSION_START,
         "command_line": "node C:/app/opencode/bin/opencode tui", "environ": ""},
    ]

    before, targets = service.plan(table)

    assert before == SESSION_START
    assert targets == []


def test_a_node_child_of_the_server_is_never_mistaken_for_a_session():
    """I figli del server sono proprio quelli da raccogliere: se contassero come sessione, niente si raccoglierebbe."""
    table = [
        _server(),
        {"pid": 61, "ppid": SERVER_PID, "name": "node.exe", "created": NEW,
         "command_line": "node C:/app/opencode/mcp/playwright-human-safe.mjs", "environ": ""},
    ]

    before, targets = service.plan(table)

    assert before is None
    assert {row["pid"] for row in targets} == {61}


# --- la raccolta -------------------------------------------------------------


def test_collect_stops_them_and_says_how_many():
    killer = Killer()

    result = service.collect(table=_table_with_an_old_and_a_new_child(), killer=killer)

    assert set(killer.stopped) == {11, 12}
    assert sorted(result["closed"]) == [11, 12]
    assert result["children"] == 3
    assert result["stale"] == 2


def test_collect_with_nothing_old_stops_nothing():
    killer = Killer()
    table = [_server(), _session(), _child(21, NEW)]

    result = service.collect(table=table, killer=killer)

    assert killer.stopped == []
    assert result["closed"] == []


def test_collect_without_a_readable_table_is_a_note(monkeypatch):
    def refuse() -> list[dict]:
        raise processes.ProcessError("PowerShell non ha risposto (TimeoutExpired)")

    monkeypatch.setattr(service.processes, "rows", refuse)

    result = service.collect()

    assert "PowerShell" in result["error"]
    assert result["closed"] == []
    assert "non leggibili" in service.lines(result)[0]


def test_a_killer_that_fails_is_not_swallowed():
    killer = Killer(fail=(12,))

    with pytest.raises(processes.ProcessError):
        service.collect(table=_table_with_an_old_and_a_new_child(), killer=killer)


# --- le righe che il proprietario legge --------------------------------------


def test_the_lines_say_the_children_and_the_moment_that_decides():
    lines = service.lines(service.survey(_table_with_an_old_and_a_new_child()))

    assert "3 processi diretti" in lines[0] and "2 di sessioni chiuse" in lines[0]
    assert "sessione attiva più vecchia" in lines[1]
    assert len(lines) == 2


def test_the_lines_say_when_no_session_is_alive():
    lines = service.lines(service.survey([_server(), _child(11, OLD)]))

    assert "nessuna sessione attiva" in lines[1]


def test_the_lines_count_what_was_collected():
    result = service.collect(table=_table_with_an_old_and_a_new_child(), killer=Killer())

    assert "raccolti: 2 processi" in service.lines(result)[-1]


def test_a_machine_without_a_shared_server_has_nothing_to_collect():
    killed = Killer()

    result = service.collect(table=[_session()], killer=killed)

    assert killed.stopped == [] and result["servers"] == 0
