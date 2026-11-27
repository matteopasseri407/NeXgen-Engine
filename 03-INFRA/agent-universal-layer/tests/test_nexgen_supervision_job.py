"""Il job object di un'unità: il kernel chiude l'albero, e chi resta fuori viene detto.

Quello che si prova è il contratto di `supervision_job`: il nome del job, che su
POSIX non c'è nessun job e nessuna chiamata finge il contrario, la misura
documentata delle strutture Win32 (una struttura sbagliata non fallisce, mente),
un rifiuto del kernel che non si ingoia, e i processi che il job non teneva, che
sono esattamente quelli da fermare a mano. Gli ultimi test parlano al kernel vero
e girano solo su Windows.
"""
from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from nexgen_local import supervision_job as jobs
from nexgen_local import supervision_processes as processes

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


def test_the_job_name_names_the_task_and_the_unit():
    assert jobs.job_name("2026-11-15-prova", "Unità 1") == "nexgen-sup:2026-11-15-prova:unita-1"
    assert jobs.job_name("2026-11-15-prova", "unita-1") == jobs.job_name("2026-11-15-prova", "Unità 1")


def test_posix_has_no_job_object_and_says_so(monkeypatch):
    """Senza kernel32 (POSIX) nessuna funzione tocca l'API dei job: lo si simula anche su Windows."""
    monkeypatch.setattr(jobs, "_kernel32", None)
    assert jobs.available() is False
    assert jobs.adopt("nexgen-sup:prova:prova", os.getpid()) == ""
    assert jobs.terminate("nexgen-sup:prova:prova", []) == {"job": False, "members": [], "strays": []}
    assert jobs.held("nexgen-sup:prova:prova") is None


@pytest.mark.skipif(struct.calcsize("P") != 8, reason="le misure documentate sono quelle a 64 bit")
def test_the_kernel_structures_have_the_documented_size():
    """Una struttura con i campi nel posto sbagliato non fallisce: il kernel legge un'altra cosa."""
    assert ctypes.sizeof(jobs._BasicLimitInformation) == 64
    assert ctypes.sizeof(jobs._ExtendedLimitInformation) == 144


def test_nothing_is_adopted_without_a_name_or_a_pid():
    assert jobs.adopt("", os.getpid()) == ""
    assert jobs.adopt("nexgen-sup:prova:prova", None) == ""


@pytest.mark.skipif(os.name != "nt", reason="solo Windows ha un kernel che risponde a queste chiamate")
def test_a_job_the_kernel_refuses_is_reported_not_ignored(monkeypatch):
    """Un job che il kernel non concede non può diventare un turno che gira senza protezione."""
    class Refusing:
        @staticmethod
        def CreateJobObjectW(*_args):
            return 0

    monkeypatch.setattr(jobs, "_kernel32", Refusing())
    monkeypatch.setattr(jobs.ctypes, "FormatError", lambda code: "rifiutato", raising=False)

    with pytest.raises(jobs.JobError, match="creazione"):
        jobs.adopt("nexgen-sup:prova:prova", os.getpid())


def test_a_process_that_cannot_be_opened_is_not_held_and_not_an_error(monkeypatch):
    """Un pid che non si apre (già uscito, o non nostro) non ferma il turno: il job non lo tiene."""
    class Kernel:
        @staticmethod
        def CreateJobObjectW(*_args):
            return 1

        @staticmethod
        def SetInformationJobObject(*_args):
            return 1

        @staticmethod
        def OpenProcess(*_args):
            return 0

    monkeypatch.setattr(jobs, "_kernel32", Kernel())
    monkeypatch.setattr(jobs, "_close", lambda handle: None)

    assert jobs.adopt("nexgen-sup:prova:prova", 4321) == ""


def test_terminate_without_a_job_says_there_was_none(monkeypatch):
    """Un'unità che ha lavorato in una scheda non ha job: non è un errore, è un fatto."""
    monkeypatch.setattr(jobs, "_open", lambda name: 0)

    assert jobs.terminate("nexgen-sup:prova:prova", []) == {
        "job": False, "members": [], "strays": [],
    }


def test_a_turn_the_kernel_will_not_hold_is_journalled_not_dropped(monkeypatch):
    """Un turno fuori dal job gira lo stesso, ma il diario dice perché: non si finge che sia tenuto."""
    from nexgen_local import supervision_harness as harness

    seen: dict = {}
    monkeypatch.setattr(
        harness.launch.jobs, "adopt",
        lambda name, pid: "processo 7 non assegnabile al job: rifiutato",
    )
    monkeypatch.setattr(
        harness.state, "append_event", lambda task, event, **fields: seen.update(fields, event=event),
    )

    harness._hold_turn("2026-11-15-prova", "prova", SimpleNamespace(pid=7))

    assert seen["event"] == "unit_unjobbed"
    assert seen["unit"] == "prova"
    assert seen["pid"] == 7
    assert seen["reason"] == "processo 7 non assegnabile al job: rifiutato"


def test_a_turn_the_job_holds_says_nothing_extra(monkeypatch):
    """Il caso normale non sporca il diario: il silence è il segnale che il kernel tiene il turno."""
    from nexgen_local import supervision_harness as harness

    monkeypatch.setattr(harness.launch.jobs, "adopt", lambda name, pid: "")
    monkeypatch.setattr(
        harness.state, "append_event",
        lambda *args, **kwargs: pytest.fail("nessun evento quando il turno è nel job"),
    )

    harness._hold_turn("2026-11-15-prova", "prova", SimpleNamespace(pid=7))


def test_the_strays_are_the_descendants_the_job_never_held(monkeypatch):
    """Chi pende da un membro senza essere nel job è ciò che il kernel non fermerà."""
    table = [
        {"pid": 10, "ppid": 0, "name": "padre"},
        {"pid": 11, "ppid": 10, "name": "figlio"},
        {"pid": 12, "ppid": 11, "name": "nipote"},
        {"pid": 20, "ppid": 0, "name": "estraneo"},
    ]

    class Kernel:
        @staticmethod
        def TerminateJobObject(handle, code):
            return 1

    monkeypatch.setattr(jobs, "_open", lambda name: 1)
    monkeypatch.setattr(jobs, "_members", lambda handle: [10])
    monkeypatch.setattr(jobs, "_kernel32", Kernel())
    monkeypatch.setattr(jobs, "_close", lambda handle: None)

    ended = jobs.terminate("nexgen-sup:prova:prova", table)

    assert ended["job"] is True
    assert ended["members"] == [10]
    assert sorted(ended["strays"]) == [11, 12], "l'estraneo non pende dal job e non si tocca"


@pytest.mark.skipif(os.name != "nt", reason="il job object esiste solo su Windows")
def test_a_real_job_holds_the_process_it_adopts_and_ends_it_in_one_call():
    """Il kernel vero: il processo adottato resta nel job anche dopo che il motore ha chiuso la presa."""
    name = jobs.job_name("prova", f"unita-{os.getpid()}")
    child = subprocess.Popen(
        SLEEPER, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert jobs.adopt(name, child.pid) == ""
        ended = jobs.terminate(name, processes.rows())
        assert ended["job"] is True
        assert child.pid in ended["members"], "il processo adottato è il job, non un pid in tabella"
        assert child.wait(timeout=20) is not None
    finally:
        jobs.terminate(name, [])
        child.kill()


@pytest.mark.skipif(os.name != "nt", reason="il job object esiste solo su Windows")
def test_a_job_bigger_than_one_pid_is_read_whole():
    """Il kernel rifiuta un buffer piccolo: la lista si rilegge alla misura che chiede."""
    name = jobs.job_name("prova", f"grande-{os.getpid()}")
    children = [
        subprocess.Popen(
            SLEEPER, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(3)
    ]
    try:
        for child in children:
            assert jobs.adopt(name, child.pid) == ""
        ended = jobs.terminate(name, [])
        assert ended["job"] is True
        assert sorted(ended["members"]) == sorted(child.pid for child in children)
    finally:
        jobs.terminate(name, [])
        for child in children:
            child.kill()


@pytest.mark.skipif(os.name != "nt", reason="il job object esiste solo su Windows")
def test_a_pid_windows_rounds_down_never_adopts_the_process_it_lands_on():
    """`OpenProcess(4321)` apre il 4320: un pid finto non deve tirare nel job il processo di un altro."""
    name = jobs.job_name("prova", f"arrotondato-{os.getpid()}")
    child = subprocess.Popen(
        SLEEPER, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert child.pid % 4 == 0, "Windows assegna pid multipli di 4"
        assert jobs.adopt(name, child.pid + 1) == ""
        ended = jobs.terminate(name, [])
        assert child.pid not in ended["members"]
        assert child.poll() is None, "il processo vero non è stato toccato"
    finally:
        jobs.terminate(name, [])
        child.kill()
