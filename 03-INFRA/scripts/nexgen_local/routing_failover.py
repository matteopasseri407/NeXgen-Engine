# nexgen: one-job reason="finish an open unit on another worker when its session ends on spent quota or stalls, once per blocked session"
"""Move a unit to another worker when its session is spent or stalled.

One job: when `sup status` finds an open unit whose current session closed on a
quota error or stood still past the threshold, mark the model exhausted, start a
new session with the same brief on the next candidate, and journal it once. A
flat model never falls back to a limited (free) one. Tier selection and the
exhaustion state live in `routing_tiers`; the status line is printed by
`supervision_review`.
"""
from __future__ import annotations

import logging
from typing import Any

from . import routing_tiers as rt
from . import supervision_costs as costs
from . import supervision_harness as harness
from . import supervision_review as review
from . import supervision_state as state
from . import supervision_stall as stall

logger = logging.getLogger(__name__)

#: A model that ran out of quota stays out of the candidate list for six hours.
QUOTA_TTL_S = rt.DEFAULT_EXHAUSTION_HOURS * 3600
#: A model whose session stalled stays out of the candidate list for one hour.
STALL_TTL_S = 3600.0
#: How much of the provider's error text is kept in the journal.
DETAIL_LIMIT = 300
QUOTA_REASON = "fine quota"
STALL_REASON = "stallo"


def unit_note(task_id: str, unit: str) -> str:
    """The one line `sup status` prints when this unit was moved or could not be moved; '' otherwise.

    Routing off (no `routing-tiers.yaml`) does nothing at all. An error here is
    printed as a line and never stops `sup status`.
    """
    if not rt.routing_tiers_exists():
        return ""
    name = state.slug(unit)
    try:
        return _move_if_blocked(task_id, name)
    except Exception as exc:  # nexgen: allow reason="un rilancio che fallisce non deve far fallire sup status: la riga lo dice"
        return f"unità {name}: rilancio non riuscito ({exc})"


def _move_if_blocked(task_id: str, name: str, *, reader=None) -> str:
    """Relaunch the unit when its current session is blocked and not yet handled."""
    record = _open_record(task_id, name)
    if not record:
        return ""
    reason, detail, ttl = _blocked(task_id, record, reader=reader)
    if not reason or _already_handled(task_id, name, record["session"]):
        return ""
    return _move(task_id, record, reason, detail, ttl)


def _open_record(task_id: str, name: str) -> dict:
    """The unit's record when it is still in progress and has a session, or an empty dict."""
    for rec in state.status(task_id)["units"]:
        if rec.get("unit") == name and rec.get("state") in stall.WATCHED_STATES and rec.get("session"):
            return rec
    return {}


def _blocked(task_id: str, record: dict, *, reader=None) -> tuple[str, str, float]:
    """Why the current session is blocked: reason, detail and how long to exclude its model."""
    found = review.delivery_failure(task_id, record["unit"], reader=reader)
    if found and found.get("session") == record["session"]:
        text = f"{found.get('error_type') or ''} {found.get('error_message') or ''}"
        if rt.is_quota_exhausted_error(text)[0]:
            return QUOTA_REASON, text.strip(), QUOTA_TTL_S
    if record.get("state") == stall.STALL_STATE:
        return STALL_REASON, "sessione ferma oltre la soglia", STALL_TTL_S
    return "", "", 0.0


def _already_handled(task_id: str, name: str, session: str) -> bool:
    """Whether the journal already names a failover for this unit and this blocked session."""
    return any(
        row.get("event") == "unit_failover" and row.get("unit") == name and row.get("session") == session
        for row in state.read_events(task_id)
    )


def _move(task_id: str, record: dict, reason: str, detail: str, ttl: float) -> str:
    """Exclude the blocked model, pick the next candidate, relaunch once and say so."""
    name, session = record["unit"], record["session"]
    model = _session_model(record)
    if not model:
        _journal(task_id, name, session, model="", next_model="", outcome="modello non leggibile",
                 reason=reason, detail=detail)
        return f"unità {name}: {reason}, modello non leggibile: nessun rilancio"
    rt.mark_exhausted(model, detail, ttl_seconds=ttl)
    limited = _is_limited(model)
    try:
        candidate = rt.pick("worker", allow_limited=limited)
    except rt.RoutingTiersError:
        line = f"worker {model} bloccato ({reason}): nessun candidato libero, nessun rilancio"
        _journal(task_id, name, session, model=model, next_model="", outcome="candidati finiti",
                 reason=reason, detail=detail)
        _alert(session, line)
        return line
    _journal(task_id, name, session, model=model, next_model=candidate["model"], outcome="rilanciata",
             reason=reason, detail=detail)
    result = _relaunch(task_id, name, candidate, limited)
    line = f"worker {model} bloccato ({reason}): rilanciato su {candidate['model']}"
    if result.get("mode") == "refused":
        line = f"worker {model} bloccato ({reason}): rilancio su {candidate['model']} rifiutato"
    _alert(session, line)
    return line


def _session_model(record: dict) -> str:
    """The model the blocked session ran on, or '' when the harness cannot say."""
    try:
        facts = costs.measure_for(str(record.get("harness") or "opencode"), record["session"])
    except costs.CostError:  # nexgen: allow reason="sessione illeggibile: senza modello nessun rilancio alla cieca"
        return ""
    return str(getattr(facts, "model", "") or "").split("#", 1)[0].strip()


def _is_limited(model: str) -> bool:
    """Whether the model is on the limited (free) tier of the owner's file."""
    tiers = rt.load_tiers().get("tiers", {})
    return model in {rt._candidate_model(c) for c in tiers.get("limited", [])}


def _relaunch(task_id: str, name: str, candidate: dict[str, Any], limited: bool) -> dict:
    """A new session with the same brief, in the unit's copy, on the chosen candidate."""
    return harness.run_unit(
        task_id, name, harness=candidate["harness"], model=candidate["model"], limited=limited, force_new=True,
    )


def _journal(task_id: str, name: str, session: str, **fields: str) -> None:
    """One `unit_failover` row: written before the relaunch, so the same blocked session is never relaunched twice."""
    fields["detail"] = fields.get("detail", "")[:DETAIL_LIMIT]
    state.append_event(task_id, "unit_failover", unit=name, session=session, **fields)


def _alert(session: str, line: str) -> None:
    """The owner's alert, once per blocked session; a failed alert never undoes a relaunch."""
    try:
        from nexgen_core.megaphone import Megaphone

        Megaphone().send_alert(
            title="Worker bloccato, unità gestita",
            message=f"{line}. Dettaglio nel diario del compito.",
            action=None,
            alert_key=f"unit_failover:{session}",
        )
    except Exception as exc:  # nexgen: allow reason="un avviso perso non annulla il rilancio già fatto"
        logger.warning("avviso di rilancio non inviato: %s", exc)
