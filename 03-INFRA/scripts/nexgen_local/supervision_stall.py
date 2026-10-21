"""A session that is active and has not moved for too long: stalled, and said once.

One job: look at the sessions of the units still in progress and write one
journal event for each that has stood still past the lane's threshold. The
threshold is the owner's (`supervision.stall_minutes`, twenty minutes when
nothing is written); a session the harness reports as stopped is finished, not
stalled, and a session nobody can measure is left alone rather than accused.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import supervision_costs as costs
from . import supervision_policy as policy
from . import supervision_state as state

#: The states a unit can be in while its session is still worth watching.
WATCHED_STATES = ("in corso", "in stallo")
#: The state a unit is in once its session was called stalled.
STALL_STATE = "in stallo"


def check(
    task_id: str, *, measure=None, now: float | None = None, minutes: float | None = None,
) -> list[dict]:
    """Journal every active unit that has stood still past the threshold, and return them.

    One event per stillness, not one per `sup status`: the row carries the
    session's own last-update instant, so asking again about the same stillness
    writes nothing, and a session that moves and stalls again gets a new row. A
    unit that was called stalled and whose session moves again goes back to work
    in the journal, so the label does not outlive the stillness it described.
    """
    limit = policy.stall_minutes() if minutes is None else float(minutes)
    moment_ms = int((now if now is not None else time.time()) * 1000)
    seen = _already_reported(task_id)
    found: list[dict] = []
    for record in state.status(task_id)["units"]:
        if record.get("state") not in WATCHED_STATES:
            continue
        session = str(record.get("session") or "")
        if not session:
            continue
        try:
            facts = (measure or costs.measure)(session)
        except costs.CostError:  # nexgen: allow reason="una sessione che non si legge non è una sessione ferma, e il motore non la accusa"
            continue
        if facts.stopped or facts.updated_ms is None:
            continue
        idle_ms = moment_ms - int(facts.updated_ms)
        if idle_ms < limit * 60_000:
            if record.get("state") == STALL_STATE:
                state.append_event(
                    task_id, "unit_resumed", unit=record["unit"], session=session,
                    measured_ms=int(facts.updated_ms),
                )
            continue
        if not _journal_is_older(record.get("at") or "", now if now is not None else time.time(), limit):
            continue
        key = (record["unit"], session, int(facts.updated_ms))
        if key in seen:
            continue
        idle_minutes = round(idle_ms / 60_000, 1)
        state.append_event(
            task_id, "unit_stalled", unit=record["unit"], session=session,
            minutes=idle_minutes, threshold=limit, measured_ms=int(facts.updated_ms),
        )
        found.append({
            "unit": record["unit"], "session": session,
            "minutes": idle_minutes, "threshold": limit,
        })
    return found


def line(stall: dict) -> str:
    """The one sentence the owner reads in `sup status`."""
    return (
        f"unità {stall['unit']}: in stallo da {stall['minutes']:.1f} minuti "
        f"(soglia {stall['threshold']:g}), possibile richiesta di permesso o turno appeso"
    )


def _already_reported(task_id: str) -> set[tuple[str, str, int]]:
    """Every stillness the journal already names, by unit, session and last update."""
    seen: set[tuple[str, str, int]] = set()
    for row in state.read_events(task_id):
        if row.get("event") != "unit_stalled":
            continue
        measured = row.get("measured_ms")
        if isinstance(measured, (int, float)):
            seen.add((str(row.get("unit") or ""), str(row.get("session") or ""), int(measured)))
    return seen


def _journal_is_older(at: str, now: float, minutes: float) -> bool:
    """Whether the unit's last journal event is also older than the threshold.

    A session whose export carries a clock that disagrees with this machine is
    not accused on that alone: the unit must have been quiet in the journal too.
    A row whose instant cannot be read says nothing, and nothing is written.
    """
    try:
        moment = datetime.fromisoformat(str(at))
    except ValueError:  # nexgen: allow reason="un istante di diario illeggibile non accusa una sessione, e il motore tace"
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (now - moment.timestamp()) >= minutes * 60
