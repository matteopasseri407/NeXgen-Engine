"""Resumable sequential relay: the same stage logic, persisted progression.

Ephemeral ``council relay`` runs stages in a local loop and forgets
everything on interruption. This module runs the identical stage
operations (``relay._select_stage_candidate`` / ``relay._invoke_stage_candidate``:
one place, never duplicated) under a LangGraph that checkpoints after
every node into SQLite inside the kept session directory.

State holds only working state (brief hash, approved sequence, completed
records, current step, attempts, quarantine deadlines, call budget): it is
NOT a second vault memory, and ``council clean`` / TTL expiry removes the
checkpoints together with the session.

Crash semantics: a ``begin_attempt`` checkpoint records that a provider
was invoked; only ``complete_attempt`` records the outcome. A checkpoint
with an invoked-but-uncompleted attempt means the provider response may
already exist (crash after response, before save): resume DECLARES this
uncertainty and refuses a silent re-invocation unless the caller passes
``allow_uncertain_rerun``. LangGraph does not make an external call
happen once; the engine declares the doubt before spending more quota.

langgraph is an optional dependency (``pip install 'nexgen-engine[council]'``,
separate from the ``[local]`` extra, no Ollama involved) and is imported
lazily so importing this module never requires it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict

from proposal import _seat_quota_pool
from relay import (
    RelayError,
    RelayQuarantine,
    RelayRecord,
    RelayStage,
    _invoke_stage_candidate,
    _load_relay_sequence,
    _refuse_no_seat,
    _select_stage_candidate,
    write_relay_verdict,
)
from seat_process import SeatRunError, _is_retryable_seat_error
from session import (
    SESSIONS_DIR,
    _set_active_session,
    _write_private_text,
    new_session_dir,
)

CHECKPOINT_NAME = "relay-checkpoints.sqlite"
IDENTITY_NAME = "relay-identity.json"


class RelayGraphState(TypedDict, total=False):
    """Working state only: JSON values, no callables, no processes."""

    brief_hash: str
    sequence_hash: str
    brief: str
    stages: list[dict[str, Any]]
    records: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    index: int
    attempt: int
    attempted: list[str]
    last_failed_pool: str
    pending: dict[str, Any] | None
    quarantine_until: dict[str, float]
    quarantine_failures: dict[str, int]
    calls_made: int
    max_seats: int
    continue_on_reject: bool
    invocation_timeout: float | None
    stop_reason: str


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def identity_hashes(brief: str, stages: list[RelayStage]) -> tuple[str, str]:
    """Identity of a resumable run: brief bytes + approved sequence.

    Resume recomputes both from the caller's arguments and refuses on any
    difference: a modified brief or sequence never silently continues an
    old run.
    """
    sequence = [{"role": stage.role, "candidates": list(stage.candidates)} for stage in stages]
    return _sha(brief), _sha(_canonical(sequence))


def _require_langgraph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RelayError(
            "[council] resumable relay needs the optional [council] extra: "
            "pip install 'nexgen-engine[council]' (no Ollama involved).",
            kind="missing_dependency",
        ) from exc
    return END, StateGraph


def _open_saver(session_dir: Path):
    """Checkpointer bound to the session dir; use as a context manager.

    v3 savers must be entered (``with``) and stay open across get_state
    and invoke calls of one operation.
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        try:
            from langgraph_checkpoint_sqlite import SqliteSaver  # noqa: F401 - legacy [council]<3 layout
        except ImportError as exc:
            raise RelayError(
                "[council] resumable relay needs the optional [council] extra: "
                "pip install 'nexgen-engine[council]' (no Ollama involved).",
                kind="missing_dependency",
            ) from exc
    return SqliteSaver.from_conn_string(str(session_dir / CHECKPOINT_NAME))


def _stage_at(state: RelayGraphState) -> dict[str, Any]:
    return state["stages"][state["index"]]


def _quarantine_from(state: RelayGraphState) -> RelayQuarantine:
    return RelayQuarantine.from_snapshot(state.get("quarantine_until", {}), state.get("quarantine_failures", {}))


def _records_as_objects(state: RelayGraphState) -> list[RelayRecord]:
    return [RelayRecord(**record) for record in state.get("records", [])]


def _node_begin_attempt(ctx: "_NodeContext") -> dict[str, Any]:
    """Checkpointed marker BEFORE any provider call: who is about to run."""
    state = ctx.state
    stage_dict = _stage_at(state)
    stage = RelayStage(role=stage_dict["role"], candidates=list(stage_dict["candidates"]))
    quarantine = _quarantine_from(state)
    chosen = _select_stage_candidate(
        stage, ctx.seats, quarantine, set(state.get("attempted", [])), state.get("last_failed_pool") or None
    )
    if chosen is None:
        # Nothing persisted here (a raising node saves no update): the
        # refusal itself is the traceable outcome, same as ephemeral.
        raise _refuse_no_seat(stage, ctx.seats, quarantine)
    attempt = int(state.get("attempt", 0)) + 1
    return {
        "attempt": attempt,
        "pending": {
            "stage_idx": state["index"] + 1,
            "attempt": attempt,
            "seat": chosen,
            "pool": _seat_quota_pool(ctx.seats[chosen]),
        },
    }


def _node_complete_attempt(ctx: "_NodeContext") -> dict[str, Any]:
    """Invoke the marked seat; record outcome or quarantine, never silently."""
    state = ctx.state
    pending = state.get("pending") or {}
    stage_dict = _stage_at(state)
    stage = RelayStage(role=stage_dict["role"], candidates=list(stage_dict["candidates"]))
    quarantine = _quarantine_from(state)
    idx = state["index"] + 1
    records = _records_as_objects(state)
    update: dict[str, Any] = {"calls_made": int(state.get("calls_made", 0)) + 1}
    try:
        record = _invoke_stage_candidate(
            idx,
            stage,
            str(pending["seat"]),
            ctx.seats,
            ctx.session_dir,
            state["brief"],
            records,
            state.get("invocation_timeout"),
            ctx.config,
        )
    except SeatRunError as e:
        attempted = list(state.get("attempted", [])) + [str(pending.get("seat", ""))]
        update["attempted"] = attempted
        trace_entry = {
            "stage_idx": idx,
            "role": stage.role,
            "seat_name": str(pending.get("seat", "")),
            "attempt": int(pending.get("attempt", 0)),
            "pool": str(pending.get("pool", "")),
            "outcome": "",
            "detail": str(e),
        }
        if not _is_retryable_seat_error(e):
            trace_entry["outcome"] = "failed"
            update["trace"] = state.get("trace", []) + [trace_entry]
            update["pending"] = None
            raise RelayError(str(e), kind="seat_failed") from e
        blocked_until = quarantine.register(str(pending.get("pool", "")))
        until, failures = quarantine.snapshot()
        update["quarantine_until"] = until
        update["quarantine_failures"] = failures
        update["last_failed_pool"] = str(pending.get("pool", ""))
        update["pending"] = None
        trace_entry["outcome"] = "quarantined"
        update["trace"] = state.get("trace", []) + [trace_entry]
        print(str(e))
        print(
            f"[council] pool '{pending.get('pool', '')}' in short quarantine until "
            f"{blocked_until.isoformat(timespec='seconds')}; trying a different pool if the sequence provides one."
        )
        return update
    update["records"] = state.get("records", []) + [record.__dict__]
    update["trace"] = state.get("trace", []) + [
        {
            "stage_idx": idx,
            "role": record.role,
            "seat_name": record.seat_name,
            "attempt": int(pending.get("attempt", 0)),
            "pool": _seat_quota_pool(ctx.seats[record.seat_name]),
            "outcome": "ok",
            "detail": "",
        }
    ]
    update["pending"] = None
    return update


def _route_after_attempt(state: RelayGraphState) -> str:
    """Next node after an attempt: same stage retries, completed advances.

    Completion is per-STAGE, not per-records: ``records`` only grows on
    success, so a quarantined attempt on stage N with N-1 records must
    route back to ``begin`` (pick the fallback for the same stage), never
    to ``next`` (which would skip the stage and index past the end).
    """
    records = state.get("records", [])
    total = len(state.get("stages", []))
    index = int(state.get("index", 0))
    if len(records) <= index:
        return "begin"
    last = records[-1]
    done = len(records)
    if last.get("verdict") == "REJECT" and not state.get("continue_on_reject") and done < total:
        return "finalize"
    if done < total:
        return "next"
    return "finalize"


def _node_next_stage(state: RelayGraphState) -> dict[str, Any]:
    return {
        "index": int(state.get("index", 0)) + 1,
        "attempt": 0,
        "attempted": [],
        "last_failed_pool": "",
        "pending": None,
    }


def _node_finalize(ctx: "_NodeContext") -> dict[str, Any]:
    state = ctx.state
    records = _records_as_objects(state)
    total = len(state.get("stages", []))
    done = len(records)
    if not records:
        return {"stop_reason": "empty"}
    if records[-1].verdict == "REJECT" and not state.get("continue_on_reject") and done < total:
        print(
            f"[council] stage {done} ({records[-1].role}): VERDICT: REJECT — "
            f"stopping the relay, skipping the remaining {total - done} stages "
            "(use --continue-on-reject to run them anyway)."
        )
        stop_reason = "rejected"
    else:
        stop_reason = "completed"
    write_relay_verdict(ctx.session_dir, records)
    print(f"[council] final verdict: {records[-1].verdict}")
    return {"stop_reason": stop_reason}


class _NodeContext:
    """Seats, session and config bound once; only JSON state crosses nodes."""

    def __init__(self, seats: dict, session_dir: Path, config: dict | None, state: RelayGraphState) -> None:
        self.seats = seats
        self.session_dir = session_dir
        self.config = config
        self.state = state


def build_relay_app(ctx_factory) -> Any:
    """Assemble the static graph: begin -> complete -> (begin | next | finalize).

    ``ctx_factory(state)`` binds seats/session/config for this process: the
    graph itself never holds a seat runner, so tests and production share
    every stage operation through ``relay``.
    """
    END, StateGraph = _require_langgraph()

    def begin(state: RelayGraphState) -> dict[str, Any]:
        return _node_begin_attempt(ctx_factory(state))

    def complete(state: RelayGraphState) -> dict[str, Any]:
        return _node_complete_attempt(ctx_factory(state))

    def finalize(state: RelayGraphState) -> dict[str, Any]:
        return _node_finalize(ctx_factory(state))

    builder = StateGraph(RelayGraphState)
    builder.add_node("begin", begin)
    builder.add_node("complete", complete)
    builder.add_node("next", _node_next_stage)
    builder.add_node("finalize", finalize)
    builder.set_entry_point("begin")
    builder.add_edge("begin", "complete")
    builder.add_conditional_edges(
        "complete",
        _route_after_attempt,
        {"begin": "begin", "next": "next", "finalize": "finalize"},
    )
    builder.add_edge("next", "begin")
    builder.add_edge("finalize", END)
    return builder


def _initial_state(
    brief: str,
    stages: list[RelayStage],
    max_seats: int,
    continue_on_reject: bool,
    invocation_timeout: float | None,
) -> RelayGraphState:
    brief_hash, sequence_hash = identity_hashes(brief, stages)
    return RelayGraphState(
        brief_hash=brief_hash,
        sequence_hash=sequence_hash,
        brief=brief,
        stages=[{"role": stage.role, "candidates": list(stage.candidates)} for stage in stages],
        records=[],
        trace=[],
        index=0,
        attempt=0,
        attempted=[],
        last_failed_pool="",
        pending=None,
        quarantine_until={},
        quarantine_failures={},
        calls_made=0,
        max_seats=max_seats,
        continue_on_reject=continue_on_reject,
        invocation_timeout=invocation_timeout,
        stop_reason="",
    )


def _write_identity(
    session_dir: Path,
    brief_hash: str,
    sequence_hash: str,
    max_seats: int,
    continue_on_reject: bool,
    invocation_timeout: float | None,
) -> None:
    _write_private_text(
        session_dir / IDENTITY_NAME,
        json.dumps(
            {
                "brief_hash": brief_hash,
                "sequence_hash": sequence_hash,
                "max_seats": max_seats,
                "continue_on_reject": continue_on_reject,
                "invocation_timeout": invocation_timeout,
            },
            indent=1,
        )
        + "\n",
    )


def _summary(session_dir: Path, state: RelayGraphState) -> dict[str, Any]:
    records = state.get("records", [])
    summary: dict[str, Any] = {
        "status": state.get("stop_reason") or "error",
        "completed": len(records),
        "total": len(state.get("stages", [])),
        "calls_made": int(state.get("calls_made", 0)),
        "session_dir": str(session_dir),
    }
    if records:
        summary["final_verdict"] = records[-1].get("verdict", "")
        summary["final_response"] = records[-1].get("response", "")
    return summary


def _resolve_session(session_ref: str) -> Path:
    candidate = Path(session_ref).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    direct = (SESSIONS_DIR / session_ref).resolve()
    if direct.is_dir():
        return direct
    raise RelayError(
        f"[council] cannot resume: session '{session_ref}' not found "
        f"(looked in {SESSIONS_DIR}). Expired sessions are removed with their checkpoints.",
        kind="session_missing",
    )


def start_resumable_relay(
    *,
    question: str,
    context: str | None,
    diff: str | None,
    sequence_spec: str,
    max_seats: int,
    continue_on_reject: bool,
    invocation_timeout: float | None,
) -> dict[str, Any]:
    """Fresh resumable run with the ephemeral path's validation and gates."""
    from proposal import load_config, load_seats
    from verdict import build_brief
    from session import egress_gate

    config = load_config()
    seats = load_seats()
    fake_args = SimpleNamespace(sequence=sequence_spec, max_seats=max_seats)
    stages = _load_relay_sequence(fake_args, config, seats)
    brief = build_brief(question, context, diff)
    egress_gate(brief)

    session_dir = new_session_dir(question)
    _set_active_session(session_dir, True)
    try:
        _write_private_text(session_dir / "00-brief.md", brief)
        print(f"[council] session kept: {session_dir}")
        print(f"[council] mode: relay (resumable) — stages: {len(stages)}")
        state = _initial_state(brief, stages, max_seats, continue_on_reject, invocation_timeout)
        _write_identity(
            session_dir,
            state["brief_hash"],
            state["sequence_hash"],
            max_seats,
            continue_on_reject,
            invocation_timeout,
        )
        with _open_saver(session_dir) as saver:
            app = build_relay_app(lambda s: _NodeContext(seats, session_dir, config, s)).compile(checkpointer=saver)
            thread = {"configurable": {"thread_id": session_dir.name}}
            final = app.invoke(state, config=thread)
            return _summary(session_dir, final)
    finally:
        _set_active_session(None)


def _uncertain_pending(state: RelayGraphState) -> dict[str, Any] | None:
    """An invoked-but-uncompleted attempt with no record for its stage."""
    pending = state.get("pending")
    if not pending:
        return None
    idx = int(pending.get("stage_idx", 0))
    if len(state.get("records", [])) >= idx:
        return None
    return pending


def resume_relay_session(
    *,
    session_ref: str,
    question: str,
    context: str | None,
    diff: str | None,
    sequence_spec: str,
    max_seats: int,
    continue_on_reject: bool,
    invocation_timeout: float | None,
    allow_uncertain_rerun: bool = False,
) -> dict[str, Any]:
    """Continue a kept session after interruption; refuse on any mismatch.

    No seat is invoked before identity (brief + sequence) matches and any
    crash-after-response uncertainty is either absent or explicitly
    accepted with ``allow_uncertain_rerun``.
    """
    from proposal import load_config, load_seats
    from verdict import build_brief
    from session import egress_gate

    session_dir = _resolve_session(session_ref)
    identity_path = session_dir / IDENTITY_NAME
    if not identity_path.is_file():
        raise RelayError(
            f"[council] cannot resume {session_dir.name}: not a resumable relay session "
            "(no identity file). Ephemeral sessions leave nothing to resume.",
            kind="session_missing",
        )
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RelayError(
            f"[council] cannot resume {session_dir.name}: unreadable identity file ({exc}).",
            kind="session_missing",
        ) from exc

    config = load_config()
    seats = load_seats()
    fake_args = SimpleNamespace(sequence=sequence_spec, max_seats=max_seats)
    stages = _load_relay_sequence(fake_args, config, seats)
    brief = build_brief(question, context, diff)
    egress_gate(brief)
    brief_hash, sequence_hash = identity_hashes(brief, stages)
    if brief_hash != identity.get("brief_hash"):
        raise RelayError(
            f"[council] cannot resume {session_dir.name}: the brief does not match "
            "the stored run (question/context/diff changed). Resume is refused.",
            kind="brief_mismatch",
        )
    if sequence_hash != identity.get("sequence_hash"):
        raise RelayError(
            f"[council] cannot resume {session_dir.name}: the sequence does not match "
            "the stored run. Resume is refused.",
            kind="sequence_mismatch",
        )
    stored_flags = (identity.get("max_seats"), identity.get("continue_on_reject"), identity.get("invocation_timeout"))
    if (max_seats, continue_on_reject, invocation_timeout) != stored_flags:
        print(
            "[council] note: run policy flags differ from the stored run; "
            "the stored policy applies to the resumed relay."
        )

    saver_cm = _open_saver(session_dir)
    with saver_cm as saver:
        app = build_relay_app(lambda s: _NodeContext(seats, session_dir, config, s)).compile(checkpointer=saver)
        thread = {"configurable": {"thread_id": session_dir.name}}
        snapshot = app.get_state(thread)
        if not snapshot.values:
            raise RelayError(
                f"[council] cannot resume {session_dir.name}: no checkpoint found. "
                "The session never started or its checkpoints were removed.",
                kind="session_missing",
            )
        state = snapshot.values
        if state.get("stop_reason") in ("completed", "rejected"):
            print(f"[council] session {session_dir.name} already {state['stop_reason']}: nothing to resume.")
            return _summary(session_dir, state)
        uncertain = _uncertain_pending(state)
        if uncertain is not None and not allow_uncertain_rerun:
            raise RelayError(
                f"[council] cannot resume {session_dir.name}: stage {uncertain.get('stage_idx')} "
                f"invoked seat '{uncertain.get('seat')}' (attempt {uncertain.get('attempt')}) but no "
                "outcome was saved — the provider response may already exist. Re-invoking may "
                "bill quota twice. Resume with --allow-uncertain-rerun to declare you accept that.",
                kind="uncertain_rerun",
            )
        if uncertain is not None:
            print(
                f"[council] UNCERTAIN rerun accepted: stage {uncertain.get('stage_idx')} "
                f"seat '{uncertain.get('seat')}' may already have responded; invoking again."
            )
        _set_active_session(session_dir, True)
        try:
            final = app.invoke(None, config=thread)
            return _summary(session_dir, final)
        finally:
            _set_active_session(None)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI probe
    print("relay_graph: use `council relay --resumable` / `--resume SESSION`.", file=sys.stderr)
    return 2
