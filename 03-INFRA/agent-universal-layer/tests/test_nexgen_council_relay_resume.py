"""Test del relay riprendibile: parità, interruzioni, quote, rifiuti, pulizia.

Nessun modello viene mai chiamato: run_seat è sempre finto. Qui si verifica
il contratto di orchestrazione (checkpoint, ripresa, identità, quarantene),
non i seggi.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import relay
import relay_graph
import session
from relay import RelayError, RelayQuarantine, RelayRecord, RelayStage
from relay_graph import (
    _initial_state,
    _uncertain_pending,
    build_relay_app,
    identity_hashes,
    resume_relay_session,
    start_resumable_relay,
)
from seat_process import SeatRunError


class SimulatedCrash(RuntimeError):
    """A dead process between two checkpoints (kill -9, SIGTERM, OOM)."""


def _seats() -> dict:
    def seat(pool: str, model: str) -> dict:
        return {"cli": "opencode", "model": model, "quota_pool": pool}

    return {"sa": seat("pool-a", "fake/a"), "sb": seat("pool-b", "fake/b"), "sc": seat("pool-a", "fake/c")}


def _stages() -> list[RelayStage]:
    return [
        RelayStage(role="r1", candidates=["sa", "sb"]),
        RelayStage(role="r2", candidates=["sc"]),
    ]


class FakeRunner:
    """Scripted seat runner: script entries are consumed in call order."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.script: list = []

    def __call__(self, seat: dict, prompt: str, session_dir: Path, timeout: float):
        model = seat["model"]
        self.calls.append(model)
        action = self.script.pop(0) if self.script else ("ok", "APPROVE", "tutto bene")
        kind = action[0]
        if kind == "ok":
            _, verdict, body = action
            return f"{body}\nVERDICT: {verdict}", {}
        if kind == "retryable":
            raise SeatRunError(f"[council] fake quota esaurita su {model}", "process_error")
        if kind == "fatal":
            raise SeatRunError(f"[council] fake errore definitivo su {model}", "invalid_timeout")
        if kind == "crash":
            raise SimulatedCrash("fake morte di processo")
        raise AssertionError(f"azione fake sconosciuta: {action}")


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


def _ctx(seats: dict, session_dir: Path):
    from relay_graph import _NodeContext

    return lambda state: _NodeContext(seats, session_dir, None, state)


def _saver_cm(session_dir: Path):
    from langgraph.checkpoint.sqlite import SqliteSaver

    return SqliteSaver.from_conn_string(str(session_dir / "relay-checkpoints.sqlite"))


def _run_app(session_dir: Path, seats: dict, state, thread_id: str):
    """One operation, one saver lifetime: open, invoke, close."""
    with _saver_cm(session_dir) as saver:
        app = build_relay_app(_ctx(seats, session_dir)).compile(checkpointer=saver)
        return app.invoke(state, config={"configurable": {"thread_id": thread_id}})


def _read_state(session_dir: Path, thread_id: str):
    with _saver_cm(session_dir) as saver:
        app = build_relay_app(_ctx({}, session_dir)).compile(checkpointer=saver)
        return app.get_state({"configurable": {"thread_id": thread_id}})


def _brief_stages():
    return "brief finto", _stages()


def test_parity_ephemeral_vs_graph(tmp_path: Path, runner: FakeRunner) -> None:
    """Stessi fake, stessi record e stessi file: il grafo non cambia il lavoro."""
    seats, (brief, stages) = _seats(), _brief_stages()
    runner.script = [("ok", "APPROVE", "prima"), ("ok", "REVISE", "seconda")]
    ephemeral_dir = tmp_path / "ephemeral"
    ephemeral_dir.mkdir()
    records: list[RelayRecord] = []
    quarantine = RelayQuarantine()
    trace: list = []
    for idx, stage in enumerate(stages, 1):
        records.append(
            relay._run_relay_stage(idx, stage, seats, ephemeral_dir, brief, records, quarantine, None, None, trace)
        )
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    runner.script = [("ok", "APPROVE", "prima"), ("ok", "REVISE", "seconda")]
    final = _run_app(graph_dir, seats, _initial_state(brief, stages, 5, False, None), "parity")
    assert [(r["role"], r["seat_name"], r["verdict"], r["response"]) for r in final["records"]] == [
        (r.role, r.seat_name, r.verdict, r.response) for r in records
    ]
    for name in ("01-sa-relay-r1.md", "02-sc-relay-r2.md"):
        assert (ephemeral_dir / name).read_text(encoding="utf-8") == (graph_dir / name).read_text(encoding="utf-8")
    assert [a.outcome for a in trace] == ["ok", "ok"]


def test_interrupt_each_stage_resumes_without_recall(tmp_path: Path, runner: FakeRunner) -> None:
    """Morte dopo il primo stadio: la ripresa non richiama il completato."""
    seats, (brief, stages) = _seats(), _brief_stages()
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    runner.script = [("ok", "APPROVE", "prima"), ("crash",)]
    with pytest.raises(SimulatedCrash):
        _run_app(graph_dir, seats, _initial_state(brief, stages, 5, False, None), "crash")
    # Lo stadio schiantato risulta invocato ma non completato: la ripresa
    # non richiama lo stadio 1 (completato) e riesegue solo lo stadio 2.
    assert runner.calls == ["fake/a", "fake/c"]
    # Nuovo processo, stesso thread: riprende dallo checkpoint.
    runner.script = [("ok", "REVISE", "seconda")]
    final = _run_app(graph_dir, seats, None, "crash")
    assert runner.calls == ["fake/a", "fake/c", "fake/c"]
    assert [r["seat_name"] for r in final["records"]] == ["sa", "sc"]
    assert final["stop_reason"] == "completed"


def test_quota_uses_only_authorized_fallbacks(tmp_path: Path, runner: FakeRunner) -> None:
    """Pool esaurita: il fallback dichiarato corre, gli altri mai."""
    seats, (brief, _) = _seats(), _brief_stages()
    stages = [RelayStage(role="r1", candidates=["sa", "sb"])]
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    runner.script = [("retryable",), ("ok", "APPROVE", "da sb")]
    final = _run_app(graph_dir, seats, _initial_state(brief, stages, 5, False, None), "q")
    assert [r["seat_name"] for r in final["records"]] == ["sb"]
    assert runner.calls == ["fake/a", "fake/b"]
    assert "pool-a" in final["quarantine_until"]
    assert final["calls_made"] == 2


def test_quota_with_no_fallback_refuses(tmp_path: Path, runner: FakeRunner) -> None:
    """Solo pool esaurita: rifiuto con lo stesso messaggio del percorso effimero."""
    import relay as relay_module

    seats, (brief, _) = _seats(), _brief_stages()
    stages = [RelayStage(role="r1", candidates=["sa"])]
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    runner.script = [("retryable",)]
    with pytest.raises(RelayError) as exc:
        _run_app(graph_dir, seats, _initial_state(brief, stages, 5, False, None), "q2")
    assert exc.value.kind == "no_seat_available"
    assert "no seat available" in str(exc.value)
    # Stesso messaggio del loop effimero, a parità di fake.
    ephemeral_dir = tmp_path / "ephemeral"
    ephemeral_dir.mkdir()
    runner.script = [("retryable",)]
    with pytest.raises(RelayError) as exc2:
        relay_module._run_relay_stage(1, stages[0], seats, ephemeral_dir, brief, [], RelayQuarantine(), None, None)
    assert str(exc.value) == str(exc2.value)


def test_reject_stops_and_writes_verdict(tmp_path: Path, runner: FakeRunner) -> None:
    """REJECT al primo stadio: il secondo non parte mai, verdetto scritto."""
    seats, (brief, stages) = _seats(), _brief_stages()
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    runner.script = [("ok", "REJECT", "pericoloso")]
    final = _run_app(graph_dir, seats, _initial_state(brief, stages, 5, False, None), "r")
    assert runner.calls == ["fake/a"]
    assert final["stop_reason"] == "rejected"
    verdict = (graph_dir / "verdict.md").read_text(encoding="utf-8")
    assert "verdict=REJECT" in verdict


def test_uncertain_rerun_refused_then_allowed(
    tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, sandbox: Path
) -> None:
    """Crash dopo la risposta, prima del salvataggio: prima dichiara, poi (con flag) riesegue."""
    import relay as relay_module

    _patch_loaders(monkeypatch)
    session_name = _start_crashing(monkeypatch, runner, sandbox, relay_module)
    session_dir = sandbox / session_name
    assert (session_dir / "relay-checkpoints.sqlite").is_file()
    # Senza flag: rifiuto senza invocare.
    before = list(runner.calls)
    with pytest.raises(RelayError) as exc:
        resume_relay_session(
            session_ref=session_name,
            question="domanda?",
            context=None,
            diff=None,
            sequence_spec="r1=sa|sb,r2=sc",
            max_seats=5,
            continue_on_reject=False,
            invocation_timeout=None,
            allow_uncertain_rerun=False,
        )
    assert exc.value.kind == "uncertain_rerun"
    assert runner.calls == before
    # Con flag: completa, con una sola re-invocazione.
    runner.script = [("ok", "REVISE", "seconda")]
    summary = resume_relay_session(
        session_ref=session_name,
        question="domanda?",
        context=None,
        diff=None,
        sequence_spec="r1=sa|sb,r2=sc",
        max_seats=5,
        continue_on_reject=False,
        invocation_timeout=None,
        allow_uncertain_rerun=True,
    )
    assert summary["status"] == "completed"
    assert summary["completed"] == 2
    assert runner.calls == before + ["fake/a", "fake/c"]


def _patch_loaders(monkeypatch: pytest.MonkeyPatch) -> None:
    import proposal
    import verdict as verdict_module

    monkeypatch.setattr(proposal, "load_config", lambda: {"seats": _seats()})
    monkeypatch.setattr(proposal, "load_seats", _seats)
    monkeypatch.setattr(verdict_module, "build_brief", lambda q, c, d=None: f"brief:{q}")
    monkeypatch.setattr(session, "egress_gate", lambda brief: None)


def _start_crashing(monkeypatch, runner: FakeRunner, sandbox: Path, relay_module) -> str:
    """Avvia un run che muore dopo la prima risposta, prima del commit."""

    real_write = relay_module._write_private_text
    state = {"fail_once": True}

    def flaky_write(path: Path, text: str) -> None:
        if state["fail_once"] and path.name.endswith("-relay-r1.md"):
            state["fail_once"] = False
            raise SimulatedCrash("morte dopo la risposta")
        return real_write(path, text)

    monkeypatch.setattr(relay_module, "_write_private_text", flaky_write)
    runner.script = [("ok", "APPROVE", "prima"), ("ok", "REVISE", "seconda")]
    with pytest.raises(SimulatedCrash):
        start_resumable_relay(
            question="domanda?",
            context=None,
            diff=None,
            sequence_spec="r1=sa|sb,r2=sc",
            max_seats=5,
            continue_on_reject=False,
            invocation_timeout=None,
        )
    assert runner.calls == ["fake/a"]
    names = [p.name for p in sandbox.iterdir() if p.is_dir()]
    assert len(names) == 1
    # Il checkpoint ha il marker invocato senza record: incertezza rilevabile.
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        from langgraph_checkpoint_sqlite import SqliteSaver

    snapshot = _read_state(sandbox / names[0], names[0])
    assert _uncertain_pending(snapshot.values) is not None
    monkeypatch.setattr(relay_module, "_write_private_text", real_write)
    return names[0]


def test_resume_refuses_modified_brief(
    tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, sandbox: Path
) -> None:
    """Brief diverso => rifiuto, zero invocazioni."""
    _patch_loaders(monkeypatch)
    runner.script = [("ok", "APPROVE", "prima"), ("ok", "APPROVE", "seconda")]
    summary = start_resumable_relay(
        question="domanda?",
        context=None,
        diff=None,
        sequence_spec="r1=sa|sb,r2=sc",
        max_seats=5,
        continue_on_reject=False,
        invocation_timeout=None,
    )
    assert summary["status"] == "completed"
    session_name = [p.name for p in sandbox.iterdir() if p.is_dir()][0]
    before = list(runner.calls)
    with pytest.raises(RelayError) as exc:
        resume_relay_session(
            session_ref=session_name,
            question="domanda DIVERSA?",
            context=None,
            diff=None,
            sequence_spec="r1=sa|sb,r2=sc",
            max_seats=5,
            continue_on_reject=False,
            invocation_timeout=None,
        )
    assert exc.value.kind == "brief_mismatch"
    assert runner.calls == before


def test_resume_missing_and_cleaned_sessions_refuse(
    runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, sandbox: Path
) -> None:
    """Sessione cancellata o scaduta: niente ripresa, checkpoint spariti con lei."""
    _patch_loaders(monkeypatch)
    with pytest.raises(RelayError) as exc:
        resume_relay_session(
            session_ref="inesistente",
            question="domanda?",
            context=None,
            diff=None,
            sequence_spec="r1=sa",
            max_seats=5,
            continue_on_reject=False,
            invocation_timeout=None,
        )
    assert exc.value.kind == "session_missing"
    assert runner.calls == []

    runner.script = [("ok", "APPROVE", "prima")]
    summary = start_resumable_relay(
        question="domanda?",
        context=None,
        diff=None,
        sequence_spec="r1=sa",
        max_seats=5,
        continue_on_reject=False,
        invocation_timeout=None,
    )
    assert summary["status"] == "completed"
    session_dir = sandbox / [p.name for p in sandbox.iterdir() if p.is_dir()][0]
    assert (session_dir / "relay-checkpoints.sqlite").is_file()
    removed = session._cleanup_sessions(0, remove_all=True)
    assert removed == 1
    assert not session_dir.exists()
    with pytest.raises(RelayError) as exc2:
        resume_relay_session(
            session_ref=session_dir.name,
            question="domanda?",
            context=None,
            diff=None,
            sequence_spec="r1=sa",
            max_seats=5,
            continue_on_reject=False,
            invocation_timeout=None,
        )
    assert exc2.value.kind == "session_missing"


def test_completed_resume_is_noop(runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, sandbox: Path) -> None:
    """Run terminato: la ripresa non richiama nulla."""
    _patch_loaders(monkeypatch)
    runner.script = [("ok", "APPROVE", "prima")]
    start_resumable_relay(
        question="domanda?",
        context=None,
        diff=None,
        sequence_spec="r1=sa",
        max_seats=5,
        continue_on_reject=False,
        invocation_timeout=None,
    )
    session_name = [p.name for p in sandbox.iterdir() if p.is_dir()][0]
    before = list(runner.calls)
    summary = resume_relay_session(
        session_ref=session_name,
        question="domanda?",
        context=None,
        diff=None,
        sequence_spec="r1=sa",
        max_seats=5,
        continue_on_reject=False,
        invocation_timeout=None,
    )
    assert summary["status"] == "completed"
    assert runner.calls == before


def test_identity_hashes_catch_sequence_changes() -> None:
    """Stesso brief, sequenza diversa: hash diversi, la ripresa rifiuterebbe."""
    brief = "brief:domanda?"
    _, seq_a = identity_hashes(brief, [RelayStage("r1", ["sa"])])
    _, seq_b = identity_hashes(brief, [RelayStage("r1", ["sb"])])
    assert seq_a != seq_b
    hash_a, _ = identity_hashes(brief, [RelayStage("r1", ["sa"])])
    hash_b, _ = identity_hashes("brief:altro?", [RelayStage("r1", ["sa"])])
    assert hash_a != hash_b
