"""La tabella dei processi: chi gira, chi sta sotto chi, e chi è il servizio condiviso.

Il ramo Linux si prova su una finta `/proc` costruita in una cartella temporanea,
quindi gira anche su Windows; niente qui avvia o ferma un processo vero.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_local import supervision_processes as processes


def _proc(root: Path, pid: int, *, comm: str, ppid: int, argv: list[str], env: list[str]) -> None:
    """One fake `/proc/<pid>` directory, with the three files the reader uses."""
    folder = root / str(pid)
    folder.mkdir(parents=True)
    (folder / "stat").write_text(f"{pid} ({comm}) S {ppid} 0 0 0 -1 0 0 0\n", encoding="utf-8")
    (folder / "cmdline").write_bytes(b"".join(part.encode() + b"\0" for part in argv))
    (folder / "environ").write_bytes(b"".join(part.encode() + b"\0" for part in env))


def test_the_linux_table_is_read_from_proc(tmp_path):
    _proc(
        tmp_path, 100, comm="bash", ppid=1, argv=["bash", "-c", "opencode cartella"],
        env=["HOME=/tmp/casa", "NEXGEN_SUP_TAB=nexgen-sup:compito:esecutore"],
    )

    rows = processes._posix_rows(tmp_path)

    assert rows == [{
        "pid": 100, "ppid": 1, "name": "bash",
        "command_line": "bash -c opencode cartella",
        "environ": "HOME=/tmp/casa\0NEXGEN_SUP_TAB=nexgen-sup:compito:esecutore\0",
    }]


def test_a_process_name_with_parentheses_does_not_break_the_parent(tmp_path):
    """Il secondo campo di `/proc/<pid>/stat` può contenere spazi e parentesi."""
    _proc(tmp_path, 200, comm="a name with ) paren", ppid=100, argv=[], env=[])

    rows = processes._posix_rows(tmp_path)

    assert rows[0]["ppid"] == 100
    assert rows[0]["name"] == "a name with ) paren", "senza cmdline resta il nome di stat"


def test_a_process_that_exits_while_it_is_read_is_skipped(tmp_path):
    folder = tmp_path / "300"
    folder.mkdir()

    assert processes._posix_rows(tmp_path) == []


def test_a_table_that_cannot_be_read_says_so(tmp_path):
    with pytest.raises(processes.ProcessError, match="non leggibile"):
        processes._posix_rows(tmp_path / "mai-esistita")


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


def test_the_linux_start_time_is_field_22(tmp_path):
    """Il campo 22 di `/proc/<pid>/stat` è l'ora di avvio, in tick dall'accensione."""
    folder = tmp_path / "100"
    folder.mkdir()
    fields = ["S", "1", *(["0"] * 17), "1234"]
    (folder / "stat").write_text(f"100 (bash) {' '.join(fields)}\n", encoding="utf-8")
    (folder / "cmdline").write_bytes(b"bash\0")
    (folder / "environ").write_bytes(b"")

    rows = processes._posix_rows(tmp_path)

    assert rows[0]["created"] == 1234.0


def test_a_windows_row_carries_the_creation_time_when_cim_gives_it():
    row = processes._windows_row({
        "ProcessId": 10, "ParentProcessId": 1, "Name": "bash.exe",
        "CommandLine": "bash", "Created": 1234,
    })
    unknown = processes._windows_row({
        "ProcessId": 11, "ParentProcessId": 1, "Name": "bash.exe",
        "CommandLine": "bash", "Created": 0,
    })

    assert row["created"] == 1234.0
    assert "created" not in unknown, "un'ora che CIM non dà non diventa zero"


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
        raise AssertionError("nessun processo da fermare, nessun comando da eseguire")

    monkeypatch.setattr(processes.subprocess, "run", forbidden)

    processes.kill([])


def test_a_process_working_directory_comes_from_proc(tmp_path, monkeypatch):
    monkeypatch.setattr(processes.os, "readlink", lambda path: f"{tmp_path}")

    assert str(processes._posix_cwd(123)) == str(tmp_path)


def test_a_deleted_working_directory_is_still_the_folder_it_names(tmp_path, monkeypatch):
    monkeypatch.setattr(processes.os, "readlink", lambda path: f"{tmp_path} (deleted)")

    assert str(processes._posix_cwd(123)) == str(tmp_path)


def test_a_working_directory_that_cannot_be_read_is_not_guessed(monkeypatch):
    def refuse(path):
        raise OSError("processo sparito")

    monkeypatch.setattr(processes.os, "readlink", refuse)

    assert processes._posix_cwd(123) is None
