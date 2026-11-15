"""La tabella dei processi: chi gira, chi sta sotto chi, e chi è il servizio condiviso.

Il meccanismo è psutil su entrambe le piattaforme. I test che leggono una tabella
vera avviano da soli il processo che guardano, e non toccano niente altro sulla
macchina; il resto prova le funzioni pure su tabelle costruite qui.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from nexgen_local import supervision_processes as processes

# The two tests that read the real table start a child of their own and then look
# for it under `-n auto`: with every other worker spawning and reaping processes,
# the pid it was given can be handed out again while the table is built. Pinned to
# one worker, not skipped (observed in the full parallel suite 2026-11-15).
pytestmark = pytest.mark.xdist_group("serial")


def _start_child(**env: str) -> subprocess.Popen:
    """Un processo avviato da questo test, che dorme finché il test non lo ferma."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, **env},
    )


def _row_of(table: list[dict], pid: int) -> dict:
    """La riga di questo pid, o un errore che dice che la tabella non lo ha visto."""
    found = [row for row in table if row["pid"] == pid]
    assert found, f"il processo {pid} non è nella tabella"
    return found[0]


def test_the_table_describes_a_child_this_test_started():
    child = _start_child(NEXGEN_SUP_TAB="nexgen-sup:compito:esecutore")
    try:
        table = processes.rows()

        row = _row_of(table, child.pid)
        assert row["ppid"] == os.getpid(), "il padre è il processo che lo ha avviato"
        assert Path(row["name"]).stem.lower().startswith("python"), row["name"]
        assert "time.sleep(30)" in row["command_line"]
        assert isinstance(row["created"], float) and row["created"] > 0
        if os.name == "nt":
            assert row["environ"] == "", "su Windows l'ambiente di un altro processo non si legge"
        else:
            assert processes.has_env(row, "NEXGEN_SUP_TAB=nexgen-sup:compito:esecutore") is True
    finally:
        child.kill()
        child.wait(timeout=30)


def test_a_process_that_exits_while_it_is_read_is_not_in_the_table():
    child = _start_child()
    child.kill()
    child.wait(timeout=30)

    still_there = [
        row for row in processes.rows()
        if row["pid"] == child.pid and "time.sleep" in row["command_line"]
    ]

    assert still_there == [], "un processo già finito non compare in una tabella letta dopo"


def test_a_table_that_cannot_be_read_says_so(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise psutil.AccessDenied(0)

    monkeypatch.setattr(processes.psutil, "process_iter", refuse)

    with pytest.raises(processes.ProcessError, match="non leggibile"):
        processes.rows()


def test_the_environment_is_written_the_way_the_marker_rule_reads_it():
    text = processes._environ_text({"HOME": "/tmp/casa", "NEXGEN_SUP_TAB": "nexgen-sup:compito:esecutore"})

    assert text == "HOME=/tmp/casa\0NEXGEN_SUP_TAB=nexgen-sup:compito:esecutore\0"
    assert processes._environ_text(None) == "", "un ambiente che psutil non dà non diventa testo inventato"


def test_the_environment_entry_is_matched_exactly():
    row = {"environ": "PATH=/usr/bin\0NEXGEN_SUP_TAB=nexgen-sup:altro:esecutore\0"}

    assert processes.has_env(row, "NEXGEN_SUP_TAB=nexgen-sup:altro:esecutore") is True
    assert processes.has_env(row, "NEXGEN_SUP_TAB=nexgen-sup") is False, "non è una sottostringa"
    assert processes.has_env({"environ": ""}, "NEXGEN_SUP_TAB=x") is False


def test_the_subtree_comes_back_deepest_first():
    table = [
        {"pid": 1, "ppid": 0, "name": "init", "command_line": "", "environ": ""},
        {"pid": 10, "ppid": 1, "name": "bash", "command_line": "", "environ": ""},
        {"pid": 20, "ppid": 10, "name": "opencode", "command_line": "", "environ": ""},
        {"pid": 30, "ppid": 20, "name": "python", "command_line": "", "environ": ""},
    ]

    tree = processes.subtree(table, [10])

    assert [row["pid"] for _depth, row in tree] == [30, 20, 10]


def test_the_creation_time_comes_from_psutil_in_seconds():
    row = processes._row(
        {"pid": 100, "name": "bash", "cmdline": ["bash", "-c", "opencode"], "create_time": 1234.5}, ppid=1,
    )

    assert row["created"] == 1234.5, "l'ora di avvio è quella che psutil riporta, in secondi epoch"
    assert row == {
        "pid": 100, "ppid": 1, "name": "bash", "command_line": "bash -c opencode",
        "environ": "", "created": 1234.5,
    }


def test_a_row_without_a_creation_time_does_not_invent_zero():
    unknown = processes._row({"pid": 11, "name": "bash.exe", "cmdline": None, "create_time": None}, ppid=1)
    zero = processes._row({"pid": 12, "name": "bash.exe", "cmdline": [], "create_time": 0}, ppid=1)

    assert "created" not in unknown, "un'ora che psutil non dà non diventa zero"
    assert "created" not in zero
    assert unknown["command_line"] == "", "una riga negata da Windows non porta una riga di comando inventata"


def test_every_parent_comes_from_one_snapshot_where_psutil_offers_one(monkeypatch):
    """Su Windows `Process.ppid()` è una fotografia dell'intero sistema per processo."""
    if os.name == "nt":
        monkeypatch.setattr(processes.psutil._pswindows, "ppid_map", lambda: {7: 1})

        assert processes._parent_map([]) == {7: 1}
    else:
        fake = SimpleNamespace(pid=7, ppid=lambda: 1)

        assert processes._parent_map([fake]) == {7: 1}


def test_a_child_created_before_its_presumed_parent_is_not_followed():
    """Un pid riusato lascia `ParentProcessId` puntato al nuovo proprietario."""
    table = [
        {"pid": 10, "ppid": 1, "name": "bash", "command_line": "", "environ": "", "created": 200.0},
        {"pid": 20, "ppid": 10, "name": "opencode", "command_line": "", "environ": "", "created": 100.0},
        {"pid": 30, "ppid": 10, "name": "python", "command_line": "", "environ": "", "created": 300.0},
    ]

    tree = processes.subtree(table, [10])

    assert [row["pid"] for _depth, row in tree] == [30, 10], "il figlio nato prima del padre non è suo"


def test_the_shared_server_is_the_one_running_serve():
    server = {"name": "opencode.exe", "command_line": "opencode.exe serve --service"}
    tab = {"name": "opencode.exe", "command_line": "opencode.exe C:\\copia --prompt \"read the serve notes\""}
    python = {"name": "python.exe", "command_line": "python -m opencode serve"}

    assert processes.is_shared_server(server) is True
    assert processes.is_shared_server(tab) is False
    assert processes.is_shared_server(python) is False


def test_killing_nothing_starts_no_process(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("nessun processo da fermare, nessun comando e nessuna apertura di processo")

    monkeypatch.setattr(processes.subprocess, "run", forbidden)
    monkeypatch.setattr(processes.psutil, "Process", forbidden)

    processes.kill([])


def test_killing_a_process_this_test_started_stops_it():
    child = _start_child()
    try:
        processes.kill([child.pid])

        assert child.wait(timeout=30) is not None, "il processo è finito"
    finally:
        child.kill()
        child.wait(timeout=30)


def test_killing_a_process_that_is_already_gone_is_not_a_failure():
    child = _start_child()
    child.kill()
    child.wait(timeout=30)

    processes.kill([child.pid])

    assert child.poll() is not None


def test_killing_a_process_this_user_may_not_stop_says_so(monkeypatch):
    def refuse(_pid):
        raise psutil.AccessDenied(4321)

    monkeypatch.setattr(processes.psutil, "Process", refuse)

    with pytest.raises(processes.ProcessError, match="permesso negato"):
        processes.kill([4321])


def test_a_process_working_directory_comes_from_psutil(tmp_path, monkeypatch):
    monkeypatch.setattr(processes.psutil, "Process", lambda _pid: SimpleNamespace(cwd=lambda: str(tmp_path)))

    assert str(processes.cwd(123)) == str(tmp_path)


def test_a_deleted_working_directory_is_still_the_folder_it_names(tmp_path, monkeypatch):
    monkeypatch.setattr(
        processes.psutil, "Process", lambda _pid: SimpleNamespace(cwd=lambda: f"{tmp_path} (deleted)"),
    )

    assert str(processes.cwd(123)) == str(tmp_path)


def test_a_working_directory_that_cannot_be_read_is_not_guessed(monkeypatch):
    def gone(_pid):
        raise psutil.NoSuchProcess(123)

    monkeypatch.setattr(processes.psutil, "Process", gone)
    assert processes.cwd(123) is None, "un processo sparito non ha una cartella"

    def denied(_pid):
        raise psutil.AccessDenied(123)

    monkeypatch.setattr(processes.psutil, "Process", denied)
    assert processes.cwd(123) is None, "un processo di un altro utente non si guarda"
