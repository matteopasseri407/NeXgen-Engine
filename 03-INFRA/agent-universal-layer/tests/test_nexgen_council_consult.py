"""Test del consulto parallelo: fan-out reale, astensioni, rebuttal, budget, pulizia.

Nessun modello viene mai chiamato: il runner è sempre finto. Qui si verifica
la meccanica (parallelismo vero, cancellazione, transcript) e i tetti, non i seggi.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import session  # noqa: E402 - council modules use bare imports, sys.path first
from consult import MAX_REBUTTAL_ROUNDS, run_consult  # noqa: E402
from relay import RelayError  # noqa: E402
from seat_process import SeatRunError  # noqa: E402


def _seats() -> dict:
    def seat(pool: str, model: str) -> dict:
        return {"cli": "opencode", "model": model, "quota_pool": pool}

    return {
        "sa": seat("pool-a", "fake/a"),
        "sb": seat("pool-b", "fake/b"),
        "sc": seat("pool-c", "fake/c"),
    }


class FakeConsultRunner:
    """Runner scritturato per modello, con picco di concorrenza misurato."""

    def __init__(self, scripts: dict) -> None:
        self.scripts = {model: list(entries) for model, entries in scripts.items()}
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self._live = 0
        self.peak = 0
        self.barrier: threading.Barrier | None = None

    def __call__(self, seat: dict, prompt: str, session_dir: Path, timeout: float):
        model = seat["model"]
        with self._lock:
            self.calls.append(model)
            self._live += 1
            self.peak = max(self.peak, self._live)
        try:
            if self.barrier is not None:
                self.barrier.wait(timeout=10)
            entries = self.scripts.get(model, [])
            action = entries.pop(0) if entries else ("ok", "APPROVE", "tutto bene")
            kind = action[0]
            if kind == "ok":
                _, verdict, body = action
                return f"{body}\nVERDICT: {verdict}", {}
            if kind == "retryable":
                raise SeatRunError(f"[council] fake quota esaurita su {model}", "process_error")
            raise SeatRunError(f"[council] fake errore definitivo su {model}", "invalid_timeout")
        finally:
            with self._lock:
                self._live -= 1


def _ok(verdict: str = "APPROVE", body: str = "parere") -> tuple:
    return ("ok", verdict, body)


def test_opinions_run_in_parallel(tmp_path: Path) -> None:
    """Every seat must enter the runner before any can finish."""
    runner = FakeConsultRunner({
        "fake/a": [_ok("APPROVE", "a")],
        "fake/b": [_ok("REVISE", "b")],
        "fake/c": [_ok("APPROVE", "c")],
    })
    runner.barrier = threading.Barrier(3)
    result = run_consult(_seats(), "brief?", ["sa", "sb", "sc"], tmp_path, None, 0, runner=runner)
    assert runner.peak == 3
    assert len(result.opinions) == 3
    assert result.disagreements == [("sa", "sb"), ("sb", "sc")]
    assert result.tally == {"APPROVE": 2, "REVISE": 1}
    assert (tmp_path / "consult.md").is_file()


@pytest.mark.parametrize("bad_timeout", [0, "invalid", float("nan"), True])
def test_invalid_timeout_refuses_before_any_call(tmp_path: Path, bad_timeout) -> None:
    seats = _seats()
    seats["sb"]["timeout_seconds"] = bad_timeout
    runner = FakeConsultRunner({})
    with pytest.raises(RelayError) as error:
        run_consult(seats, "brief?", ["sa", "sb"], tmp_path, None, 0, runner=runner)
    assert error.value.kind == "invalid_timeout"
    assert runner.calls == []


def test_failure_abstains_without_aborting(tmp_path: Path) -> None:
    """Un seggio rotto si astiene, gli altri completano, tutto a verbale."""
    runner = FakeConsultRunner({
        "fake/a": [("fatal",)],
        "fake/b": [_ok("APPROVE", "b")],
    })
    result = run_consult(_seats(), "brief?", ["sa", "sb"], tmp_path, None, 0, runner=runner)
    assert [op.seat_name for op in result.opinions] == ["sb"]
    assert [ab.seat_name for ab in result.abstentions] == ["sa"]
    assert "Abstentions" in (tmp_path / "consult.md").read_text(encoding="utf-8")


def test_no_rebuttal_on_agreement(tmp_path: Path) -> None:
    """Tutti d'accordo: nessuna replica, nessuna chiamata in più."""
    runner = FakeConsultRunner({"fake/a": [_ok()], "fake/b": [_ok()]})
    result = run_consult(_seats(), "brief?", ["sa", "sb"], tmp_path, None, 1, runner=runner)
    assert result.rebuttals == []
    assert result.disagreements == []
    assert sorted(runner.calls) == ["fake/a", "fake/b"]


def test_rebuttal_only_on_disagreement(tmp_path: Path) -> None:
    """Disaccordo: una replica mirata per seggio completato, poi tally."""
    runner = FakeConsultRunner({
        "fake/a": [_ok("APPROVE", "a"), _ok("APPROVE", "resto convinto")],
        "fake/b": [_ok("REJECT", "b"), _ok("REVISE", "concedo in parte")],
    })
    result = run_consult(_seats(), "brief?", ["sa", "sb"], tmp_path, None, 1, runner=runner)
    assert len(result.rebuttals) == 2
    assert sorted(runner.calls) == ["fake/a", "fake/a", "fake/b", "fake/b"]
    assert result.disagreements == [("sa", "sb")]
    assert result.tally["APPROVE"] == 2  # opinione + replica di sa


def test_zero_opinions_refuses(tmp_path: Path) -> None:
    """Tutti falliti: rifiuto, non sintesi sul vuoto."""
    runner = FakeConsultRunner({"fake/a": [("fatal",)], "fake/b": [("retryable",)]})
    with pytest.raises(RelayError, match="no opinion"):
        run_consult(_seats(), "brief?", ["sa", "sb"], tmp_path, None, 0, runner=runner)


def test_budgets_are_hard(tmp_path: Path) -> None:
    """Oltre i tetti o seggi doppi: rifiuto prima di qualsiasi chiamata."""
    seats = {f"s{i}": {"cli": "opencode", "model": f"fake/{i}", "quota_pool": f"p{i}"} for i in range(6)}
    runner = FakeConsultRunner({})
    with pytest.raises(RelayError, match="at most"):
        run_consult(seats, "brief?", [f"s{i}" for i in range(6)], tmp_path, None, 0, runner=runner)
    with pytest.raises(RelayError, match="rebuttals"):
        run_consult(_seats(), "brief?", ["sa"], tmp_path, None, MAX_REBUTTAL_ROUNDS + 1, runner=runner)
    with pytest.raises(RelayError, match="must not repeat"):
        run_consult(_seats(), "brief?", ["sa", "sa"], tmp_path, None, 0, runner=runner)
    with pytest.raises(RelayError, match="unknown seat"):
        run_consult(_seats(), "brief?", ["fantasma"], tmp_path, None, 0, runner=runner)
    assert runner.calls == []


class FakeProc:
    def __init__(self) -> None:
        self.terminated = False
        self.alive = True
        self.pid = None

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.alive = False


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


def test_registry_tracks_and_cancels_all(clean_registry) -> None:
    """Due processi live: la cancellazione li ferma entrambi e svuota."""
    procs = [FakeProc(), FakeProc()]
    tokens = [session._register_proc(proc) for proc in procs]
    assert len(tokens) == 2 and tokens[0] != tokens[1]
    assert session._cancel_all_procs() == 2
    assert all(proc.terminated for proc in procs)
    assert session._LIVE_PROCS == {}
    session._release_proc("inesistente")  # mai un errore


def test_active_slot_and_registry_stay_consistent(clean_registry) -> None:
    """Il percorso sequenziale esistente registra e rilascia da solo."""
    proc = FakeProc()
    session._set_active_proc(proc)
    assert session._ACTIVE_PROC is proc
    assert len(session._LIVE_PROCS) == 1
    session._set_active_proc(None)
    assert session._ACTIVE_PROC is None
    assert session._LIVE_PROCS == {}


def test_best_effort_cleanup_stops_all_and_removes_dir(tmp_path: Path, clean_registry) -> None:
    """SIGTERM con due seggi: processi fermati, directory effimera rimossa."""
    victim = tmp_path / "session"
    victim.mkdir()
    (victim / "x.md").write_text("x")
    procs = [FakeProc(), FakeProc()]
    for proc in procs:
        session._register_proc(proc)
    session._ACTIVE_SESSION_DIR = victim
    session._best_effort_cleanup()
    assert all(proc.terminated for proc in procs)
    assert not victim.exists()
