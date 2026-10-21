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
