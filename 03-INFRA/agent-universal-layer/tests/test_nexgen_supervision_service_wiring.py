"""Il collegamento: la raccolta dei figli vecchi parte da sola.

Nessun test avvia OpenCode o ferma un processo vero: la tabella è finta e il killer
registra i pid. Si prova che la raccolta parte alla fine di `sup publish`, alla
chiusura di un'unità e con `sup pulizia --chiudi`, e che una raccolta che non riesce
è una nota letta dal proprietario, mai un errore che annulla la pubblicazione.
"""
from __future__ import annotations

import pytest

from nexgen_local import cli
from nexgen_local import supervision_service as service
from nexgen_local import supervision_state as state
from nexgen_local.cmds import sup as sup_cmd
from nexgen_local.supervision_state import SupervisionError

SERVER_PID = 900

COLLECTED = {
    "servers": 1, "children": 2, "stale": 1, "since": "2026-10-25T08:00:00",
    "alive": True, "closed": [11],
}


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane"


@pytest.fixture
def task(lane):
    return state.start("prova raccolta automatica")["id"]


def _table() -> list[dict]:
    """Un server con un figlio vecchio, uno nuovo e una sessione viva."""
    return [
        {"pid": SERVER_PID, "ppid": 1, "name": "opencode.exe", "created": 500.0,
         "command_line": "opencode serve --port 4096", "environ": ""},
        {"pid": 11, "ppid": SERVER_PID, "name": "python.exe", "created": 600.0,
         "command_line": "python un-mcp.py", "environ": ""},
        {"pid": 21, "ppid": SERVER_PID, "name": "node.exe", "created": 1200.0,
         "command_line": "node x.mjs", "environ": ""},
        {"pid": 4242, "ppid": 1, "name": "opencode.exe", "created": 1000.0,
         "command_line": "opencode -s ses_prova", "environ": ""},
    ]


def test_after_work_says_what_was_collected_and_journals_it(task, monkeypatch):
    monkeypatch.setattr(sup_cmd.service, "collect", lambda **kwargs: dict(COLLECTED))

    lines = sup_cmd._after_work(task, "prova")

    assert "raccolti: 1 processi" in lines[-1]
    rows = [row for row in state.read_events(task) if row.get("event") == "service_collected"]
    assert rows and rows[-1]["pids"] == [11] and rows[-1]["unit"] == "prova"


def test_after_work_never_takes_back_a_publication_that_already_landed(task, monkeypatch):
    def refuse(**kwargs):
        raise SupervisionError("tabella dei processi non leggibile")

    monkeypatch.setattr(sup_cmd.service, "collect", refuse)

    lines = sup_cmd._after_work(task, "prova")

    assert "non raccolti" in lines[0] and "non leggibile" in lines[0]
    assert not [row for row in state.read_events(task) if row.get("event") == "service_collected"]


def test_after_work_says_nothing_was_collected_when_there_was_nothing(task, monkeypatch):
    quiet = {**COLLECTED, "stale": 0, "closed": []}
    monkeypatch.setattr(sup_cmd.service, "collect", lambda **kwargs: dict(quiet))

    lines = sup_cmd._after_work(task, "prova")

    assert not any("raccolti" in line for line in lines)
    assert not [row for row in state.read_events(task) if row.get("event") == "service_collected"]


def test_pulizia_chiudi_collects_the_shared_servers_old_children(lane, capsys, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(service.processes, "rows", _table)
    monkeypatch.setattr(service.processes, "kill", lambda pids: killed.extend(pids))
    capsys.readouterr()

    assert cli.main(["sup", "pulizia", "--chiudi"]) == 0
    out = capsys.readouterr().out

    assert killed == [11], "solo il figlio vecchio: il nuovo e la sessione viva restano"
    assert "raccolti: 1 processi" in out


def test_pulizia_without_the_flag_does_not_collect(lane, capsys, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(service.processes, "rows", _table)
    monkeypatch.setattr(service.processes, "kill", lambda pids: killed.extend(pids))
    capsys.readouterr()

    assert cli.main(["sup", "pulizia"]) == 0
    out = capsys.readouterr().out

    assert killed == []
    assert "1 di sessioni chiuse" in out, "l'elenco dice cosa si raccoglierebbe"
