"""Pinning del resume relay_graph: comportamento attuale su sessioni finte.

Sessioni finte in tmp_path (directory + identity file, nessun modello reale,
nessun checkpoint SQLite reale). Fissa che:
- resume di sessione completata e un noop senza nuove chiamate,
- resume rifiuta brief modificato senza invocare,
- resume rifiuta contratto sedia cambiato senza invocare,
- sessione mancante rifiuta senza richiamare.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import relay  # noqa: E402
import relay_graph  # noqa: E402
import session  # noqa: E402
from relay import RelayError, RelayStage  # noqa: E402
from relay_graph import identity_hashes, resume_relay_session, seat_contract_hash  # noqa: E402


def _seats() -> dict:
    def seat(pool: str, model: str) -> dict:
        return {"cli": "opencode", "model": model, "quota_pool": pool}

    return {"sa": seat("pool-a", "fake/a"), "sb": seat("pool-b", "fake/b")}


class FakeRunner:
    """Sostituto di run_seat: registra ogni invocazione reale del provider."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, seat: dict, prompt: str, session_dir: Path, timeout: float):
        self.calls.append(seat["model"])
        raise AssertionError("run_seat non deve essere invocato dal resume pinnato")


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    fake = FakeRunner()
    monkeypatch.setattr(relay, "run_seat", fake)
    return fake


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(relay_graph, "SESSIONS_DIR", sessions)
    return sessions


def _patch_loaders(monkeypatch: pytest.MonkeyPatch, seats: dict) -> None:
    import proposal
    import verdict as verdict_module

    monkeypatch.setattr(proposal, "load_config", lambda: {"seats": seats})
    monkeypatch.setattr(proposal, "load_seats", lambda: seats)
    monkeypatch.setattr(verdict_module, "build_brief", lambda q, c, d=None: f"brief:{q}")
    monkeypatch.setattr(session, "egress_gate", lambda brief: None)


def _base_args(**over) -> dict:
    args = dict(
        question="domanda?",
        context=None,
        diff=None,
        sequence_spec="r1=sa",
        max_seats=5,
        continue_on_reject=False,
        invocation_timeout=None,
    )
    args.update(over)
    return args


def _write_identity(session_dir: Path, seats: dict, question: str = "domanda?") -> None:
    brief = f"brief:{question}"
    stages = [RelayStage("r1", ["sa"])]
    brief_hash, sequence_hash = identity_hashes(brief, stages)
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / relay_graph.IDENTITY_NAME).write_text(
        json.dumps(
            {
                "brief_hash": brief_hash,
                "sequence_hash": sequence_hash,
                "max_seats": 5,
                "continue_on_reject": False,
                "invocation_timeout": None,
                "seat_contract_hash": seat_contract_hash(seats, stages),
            }
        ),
        encoding="utf-8",
    )


def _fake_completed_app(monkeypatch: pytest.MonkeyPatch, invoked: list[str]):
    completed = {
        "stop_reason": "completed",
        "records": [{"verdict": "APPROVE", "response": "ok"}],
        "stages": [{"role": "r1", "candidates": ["sa"]}],
        "calls_made": 1,
    }

    class FakeApp:
        def get_state(self, thread):
            return SimpleNamespace(values=dict(completed))

        def update_state(self, *a, **k):  # pragma: no cover - noop non lo usa
            raise AssertionError("update_state non deve servire su run completato")

        def invoke(self, *a, **k):  # pragma: no cover - noop non lo usa
            invoked.append("invoke")
            raise AssertionError("invoke non deve partire su run completato")

    class FakeBuilder:
        def compile(self, checkpointer=None):
            return FakeApp()

    @contextmanager
    def fake_saver(session_dir: Path):
        yield object()

    monkeypatch.setattr(relay_graph, "_open_saver", fake_saver)
    monkeypatch.setattr(relay_graph, "build_relay_app", lambda ctx_factory: FakeBuilder())


def test_pinning_completed_resume_is_noop(runner, monkeypatch, sandbox: Path) -> None:
    seats = _seats()
    _patch_loaders(monkeypatch, seats)
    session_dir = sandbox / "completata"
    _write_identity(session_dir, seats)
    invoked: list[str] = []
    _fake_completed_app(monkeypatch, invoked)
    summary = resume_relay_session(session_ref="completata", **_base_args())
    assert summary["status"] == "completed"
    assert summary["completed"] == 1
    assert invoked == []
    assert runner.calls == []


def test_pinning_resume_rejects_modified_brief(runner, monkeypatch, sandbox: Path) -> None:
    seats = _seats()
    _patch_loaders(monkeypatch, seats)
    _write_identity(sandbox / "storica", seats, question="domanda?")
    with pytest.raises(RelayError) as exc:
        resume_relay_session(session_ref="storica", **_base_args(question="domanda DIVERSA?"))
    assert exc.value.kind == "brief_mismatch"
    assert runner.calls == []


def test_pinning_resume_rejects_changed_seat_contract(runner, monkeypatch, sandbox: Path) -> None:
    seats = _seats()
    _patch_loaders(monkeypatch, seats)
    _write_identity(sandbox / "storica", seats, question="domanda?")
    seats["sa"] = {**seats["sa"], "model": "remote/changed"}
    with pytest.raises(RelayError) as exc:
        resume_relay_session(session_ref="storica", **_base_args())
    assert exc.value.kind == "seat_contract_mismatch"
    assert runner.calls == []


def test_pinning_missing_session_refuses_without_recall(runner, monkeypatch, sandbox: Path) -> None:
    _patch_loaders(monkeypatch, _seats())
    with pytest.raises(RelayError) as exc:
        resume_relay_session(session_ref="inesistente", **_base_args())
    assert exc.value.kind == "session_missing"
    assert runner.calls == []
