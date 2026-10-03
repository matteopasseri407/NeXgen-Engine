"""Il registro processi e' usato dal percorso reale (run_seat), non solo dai test.

Regressione trovata: run_seat usava lo slot singolo, quindi due seggi
paralleli si sfrattavano a vicenda e una fine puliva anche l'altro.
Questi test guidano run_seat vero con un Popen finto e verificano che
due invocazioni sovrapposte restino entrambe registrate finche' entrambe
non hanno finito.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import seat_process  # noqa: E402
import session  # noqa: E402
from seat_process import run_seat  # noqa: E402


class FakeStdin:
    def write(self, data) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakePopen:
    """Popen finto per il seggio opencode: una riga JSONL, poi EOF bloccante."""

    def __init__(self, release: threading.Event, calls: list, name: str) -> None:
        self._release = release
        self._calls = calls
        self._name = name
        # No invented OS PID: this double must never target a real process.
        self.pid = None
        self.returncode = None
        self.stdin = FakeStdin()
        self.terminated = False

    @property
    def stdout(self):
        def _gen():
            yield '{"type":"text","part":{"text":"ciao da ' + self._name + '"}}\n'
            self._release.wait(timeout=30)

        return _gen()

    @property
    def stderr(self):
        return iter(())

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9


@pytest.fixture
def clean_registry():
    session._CLEANUP_RAN = False
    session._ACTIVE_PROC = None
    session._ACTIVE_TOKEN = ""
    session._ACTIVE_SESSION_DIR = None
    session._LIVE_PROCS.clear()
    yield
    session._LIVE_PROCS.clear()
    session._CLEANUP_RAN = False
    session._ACTIVE_PROC = None
    session._ACTIVE_TOKEN = ""
    session._ACTIVE_SESSION_DIR = None


def _seat(model: str) -> dict:
    return {"cli": "opencode", "model": model}


def test_cancellation_during_spawn_stops_the_late_process(tmp_path, monkeypatch, clean_registry):
    from seat_process import SeatRunError
    release = threading.Event()
    created = []

    def factory(*args, **kwargs):
        proc = FakePopen(release, created, "late")
        created.append(proc)
        # Cancellation occurs after OS spawn but before registration.
        session._cancel_all_procs()
        release.set()
        return proc

    monkeypatch.setattr(seat_process.subprocess, "Popen", factory)
    with pytest.raises(SeatRunError) as error:
        run_seat(_seat("fake/late"), "brief", tmp_path, 10)
    assert error.value.kind == "cancelled"
    assert created[0].terminated
    assert session._live_procs_snapshot() == []


def test_overlapping_seats_stay_registered_until_each_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_registry
) -> None:
    """A e B partono insieme: entrambi registrati; A finisce, B resta; poi vuoto."""
    release = threading.Event()
    created: list = []

    def factory(*args, **kwargs):
        proc = FakePopen(release, created, f"seat-{len(created)}")
        created.append(proc)
        return proc

    monkeypatch.setattr(seat_process.subprocess, "Popen", factory)
    answers: dict = {}
    errors: dict = {}

    def run(name: str) -> None:
        try:
            answers[name] = run_seat(_seat(f"fake/{name}"), "brief?", tmp_path, 60)
        except Exception as exc:  # noqa: BLE001 - il test deve mostrare l'errore, non perderlo
            errors[name] = exc

    thread_a = threading.Thread(target=run, args=("a",), daemon=True)
    thread_b = threading.Thread(target=run, args=("b",), daemon=True)
    thread_a.start()
    thread_b.start()
    deadline = time.time() + 10
    while len(session._LIVE_PROCS) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert len(session._LIVE_PROCS) == 2, "entrambi i seggi devono essere registrati insieme"
    release.set()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)
    assert not thread_a.is_alive() and not thread_b.is_alive()
    assert errors == {}
    # No order assumption: whichever thread wins the race takes seat-0.
    assert sorted([answers["a"][0], answers["b"][0]]) == ["ciao da seat-0", "ciao da seat-1"]
    assert session._LIVE_PROCS == {}, "a fine corsa il registro deve essere vuoto"


def test_finished_seat_releases_only_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_registry
) -> None:
    """Un seggio che finisce non cancella la registrazione dell'altro ancora vivo."""
    release = threading.Event()
    created: list = []

    def factory(*args, **kwargs):
        proc = FakePopen(release, created, f"seat-{len(created)}")
        created.append(proc)
        return proc

    monkeypatch.setattr(seat_process.subprocess, "Popen", factory)

    results: dict = {}

    def run(name: str) -> None:
        results[name] = run_seat(_seat(f"fake/{name}"), "brief?", tmp_path, 60)

    thread_a = threading.Thread(target=run, args=("a",), daemon=True)
    thread_b = threading.Thread(target=run, args=("b",), daemon=True)
    thread_a.start()
    thread_b.start()
    deadline = time.time() + 10
    while len(session._LIVE_PROCS) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert len(session._LIVE_PROCS) == 2
    # Simula A che termina per primo liberando solo il suo token.
    tokens = dict(session._LIVE_PROCS)
    assert len(tokens) == 2
    first_token = next(iter(tokens))
    session._release_proc(first_token)
    assert len(session._LIVE_PROCS) == 1, "B deve restare registrato dopo l'uscita di A"
    release.set()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)
    assert session._LIVE_PROCS == {}
