"""The supervisor's turn: one review round, APPROVA or RIMANDA, and the corrections.

A review is a file, not a sentence in a terminal: round 1 is the first look at
the unit, and every RIMANDA opens the next round. An approval names a commit
only when the last collection the engine ran said PASS and the branch still
points at that same commit, so "approved" can never describe code nobody
judged. This module writes the file, records the decision and hands the
corrections to the executor's own session; it computes no gate and pushes
nothing.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nexgen_core.files import write_private_text

from . import supervision_gates as gates
from . import supervision_harness as harness
from . import supervision_state as state
from . import supervision_turns as turns
from .supervision_state import SupervisionError

#: The file of one review round; the round starts at 1 and only goes up.
REVIEW_FILE = "45-revisione-{unit}-{round}.md"
#: Events that mean the engine handed work to the task's session.
DELIVERY_EVENTS = ("unit_started", "unit_reworked")
#: A failed turn older than its delivery by more than this belongs to the previous one.
FAILURE_SLACK_MS = 5000


class ReviewError(SupervisionError):
    """A review that cannot be written or accepted: no collection, a moved branch, an empty file."""


def review_path(task_id: str, unit: str, round_no: int) -> Path:
    """Where round `round_no` of this unit's review lives."""
    return state.open_task(task_id) / REVIEW_FILE.format(unit=state.slug(unit), round=round_no)


def next_round(task_id: str, unit: str) -> int:
    """The first round number with no file yet, so a lost file is not silently overwritten."""
    number = 1
    while review_path(task_id, unit, number).exists():
        number += 1
    return number


def last_collection(task_id: str, unit: str) -> dict:
    """The last collection of this unit, or an empty row when the engine never judged it."""
    name = state.slug(unit)
    found: dict = {}
    for row in state.read_events(task_id):
        if row.get("event") == "unit_collected" and row.get("unit") == name:
            found = row
    return found


def pending_round(task_id: str, unit: str, *, reader=None) -> int:
    """The last RIMANDA round that never reached the executor, or 0.

    A round is delivered when its `unit_reworked` event exists: that event is
    written by a run in the session and by the tab a lost session opens. A round
    whose run started and whose turn the provider then cut off is delivered on
    paper and not in the copy, so it stays pending and `--resend` may repeat it.
    """
    name = state.slug(unit)
    last = last_decision(task_id, name)
    if not last or str(last.get("verdict") or "") != state.REWORKED:
        return 0
    round_no = int(last.get("round") or 0)
    delivered = any(
        row.get("event") == "unit_reworked" and row.get("unit") == name
        and int(row.get("round") or 0) == round_no
        for row in state.read_events(task_id)
    )
    if not delivered:
        return round_no
    failure = delivery_failure(task_id, name, reader=reader)
    return round_no if int(failure.get("round") or 0) == round_no else 0


def delivery_failure(task_id: str, unit: str, *, reader=None) -> dict:
    """The last delivery of this unit whose turn the provider closed with an error, or {}.

    A run writes its event when it starts, so the journal cannot see that the
    turn produced nothing. The session can: its last turn failed with a provider
    error (`provider.content-filter` and the like) and that turn is newer than
    the delivery. An unreadable turn is not a failure: the engine says nothing
    instead of accusing a delivery it cannot measure.
    """
    name = state.slug(unit)
    delivery = _last_delivery(task_id, name)
    session = str(delivery.get("session") or "")
    if not session:
        return {}
    try:
        turn = (reader or turns.last_turn)(session)
    except turns.TurnError:  # nexgen: allow reason="un turno non leggibile non è una consegna fallita: la riga di stato tace invece di accusare"
        return {}
    if not turn.failed:
        return {}
    started = _event_ms(delivery.get("at"))
    if started and turn.at_ms and turn.at_ms < started - FAILURE_SLACK_MS:
        return {}
    return {
        "unit": name,
        "round": int(delivery.get("round") or 0),
        "session": session,
        "error_type": turn.error_type or turn.outcome,
        "error_message": turn.error_message,
        "turn_at": turn.at_ms,
    }


def note_delivery_failure(task_id: str, unit: str, *, reader=None) -> dict:
    """Name a failed delivery in the journal once, and give it back for the status line.

    `sup status` asks every time it runs, so the event is written only when this
    exact failure (same round, session and closing turn) is not there yet.
    """
    found = delivery_failure(task_id, unit, reader=reader)
    if not found:
        return {}
    for row in state.read_events(task_id):
        if (
            row.get("event") == "unit_delivery_failed"
            and row.get("unit") == found["unit"]
            and int(row.get("round") or 0) == found["round"]
            and row.get("session") == found["session"]
            and int(row.get("turn_at") or 0) == found["turn_at"]
        ):
            return found
    state.append_event(task_id, "unit_delivery_failed", **found)
    return found


def status_note(task_id: str, unit: str, *, reader=None) -> str:
    """The line `sup status` prints for a delivery the provider cut off, or an empty string."""
    found = note_delivery_failure(task_id, unit, reader=reader)
    if not found:
        return ""
    round_no = int(found.get("round") or 0)
    where = f"giro {round_no}" if round_no else "prima consegna"
    return (
        f"consegna fallita: {found['error_type']} ({where}, sessione {found['session']}): "
        "le correzioni non sono arrivate; `sup review ... --resend` le rimanda, "
        "con `-m <modello>` se il provider rifiuta quello configurato"
    )


def last_decision(task_id: str, unit: str) -> dict:
    """The unit's last review decision, or an empty row when it was never reviewed."""
    name = state.slug(unit)
    rows = [
        row for row in state.read_events(task_id)
        if row.get("event") == "unit_reviewed" and row.get("unit") == name
    ]
    return rows[-1] if rows else {}


def approve(task_id: str, unit: str, reason: str) -> dict:
    """Accept the unit at the commit the engine last passed, and write the round that says so."""
    record = state.unit_record(task_id, unit)
    name = record["unit"]
    text = (reason or "").strip()
    if not text:
        raise ReviewError("motivo vuoto: `--approve \"<perché>\"`")
    collection = last_collection(task_id, name)
    if not collection:
        raise ReviewError(f"unità {name} mai raccolta: `sup collect` prima di approvare")
    verdict = str(collection.get("verdict") or "")
    if verdict != "PASS":
        raise ReviewError(
            f"l'ultima `sup collect` di {name} ha verdetto {verdict or '?'}: un'approvazione "
            "richiede PASS. Correggi l'unità, raccogli di nuovo, poi approva"
        )
    head = _branch_head(record)
    collected = str(collection.get("head") or "")
    if not collected:
        raise ReviewError(
            f"l'ultima `sup collect` di {name} non porta la head che ha giudicato: "
            "rifai `sup collect` prima di approvare"
        )
    if head != collected:
        raise ReviewError(
            f"la head del ramo è cambiata dopo la raccolta: raccolta {collected[:12]}, "
            f"ora {head[:12]}: `sup collect` di nuovo prima di approvare"
        )
    round_no = next_round(task_id, name)
    path = review_path(task_id, name, round_no)
    write_private_text(path, _review_text(task_id, name, round_no, state.APPROVED, head, collection, text))
    state.append_event(
        task_id, "unit_reviewed", unit=name, round=round_no, verdict=state.APPROVED,
        reason=text, head=head, collected_head=collected, collect_verdict=verdict,
    )
    return {
        "unit": name, "round": round_no, "path": path, "verdict": state.APPROVED,
        "head": head, "collected_head": collected, "collect_verdict": verdict,
    }


def rework(task_id: str, unit: str, corrections: str | Path, *, sender=None) -> dict:
    """Refuse the unit for now, write the round, and put the corrections in its session."""
    record = state.unit_record(task_id, unit)
    name = record["unit"]
    path = Path(corrections).expanduser()
    if not path.is_file():
        raise ReviewError(f"file delle correzioni inesistente: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ReviewError(f"file delle correzioni vuoto: {path}")
    pending = pending_round(task_id, name)
    if pending:
        raise ReviewError(
            f"il giro {pending} di {name} non è ancora consegnato: "
            f"`sup review {task_id} {name} --resend` lo riconsegna. Un nuovo giro si apre solo dopo"
        )
    head = _branch_head(record)
    collection = last_collection(task_id, name)
    round_no = next_round(task_id, name)
    review = review_path(task_id, name, round_no)
    write_private_text(review, _review_text(task_id, name, round_no, state.REWORKED, head, collection, text))
    state.append_event(
        task_id, "unit_reviewed", unit=name, round=round_no, verdict=state.REWORKED,
        reason=f"correzioni in {path.name}", head=head,
        collected_head=str(collection.get("head") or ""),
        collect_verdict=str(collection.get("verdict") or ""),
        corrections=str(path),
    )
    delivery = (sender or harness.send_corrections)(task_id, name, review, round_no)
    return {
        "unit": name, "round": round_no, "path": review, "verdict": state.REWORKED,
        "head": head, "delivery": delivery,
    }


def resend(task_id: str, unit: str, *, sender=None, model: str = "") -> dict:
    """Deliver again the last round that never reached the executor, without a new number.

    The round file is already written and the journal already knows it: this
    only repeats the delivery, so a busy session or a missing one cannot burn a
    round number or leave the corrections behind. `model` is the owner's answer
    to a provider that refuses the configured one: it reaches `opencode run -s
    <sessione> -m <modello>` and nowhere else.
    """
    record = state.unit_record(task_id, unit)
    name = record["unit"]
    round_no = pending_round(task_id, name)
    if not round_no:
        raise ReviewError(
            f"nessun giro di {name} in attesa di consegna: `sup review ... --rework <file>` apre il prossimo"
        )
    path = review_path(task_id, name, round_no)
    if not path.is_file():
        raise ReviewError(
            f"file del giro {round_no} sparito: {path}: riscrivi le correzioni con `--rework`"
        )
    delivery = (sender or harness.send_corrections)(
        task_id, name, path, round_no, open_without_session=True, model=model,
    )
    last = last_decision(task_id, name)
    return {
        "unit": name, "round": round_no, "path": path, "verdict": state.REWORKED,
        "head": str(last.get("head") or ""), "delivery": delivery,
    }


def _last_delivery(task_id: str, name: str) -> dict:
    """The journal row of the last time work was handed to this unit's session."""
    found: dict = {}
    for row in state.read_events(task_id):
        if row.get("event") in DELIVERY_EVENTS and row.get("unit") == name:
            found = row
    return found


def _event_ms(at) -> int:
    """A journal instant in the store's own milliseconds; an unreadable one is zero."""
    try:
        return int(datetime.fromisoformat(str(at or "")).timestamp() * 1000)
    except ValueError:  # nexgen: allow reason="un istante illeggibile vale zero, e zero non scarta nessun turno"
        return 0


def _branch_head(record: dict) -> str:
    """The commit the unit's branch points at now, refused when the copy is gone."""
    folder = Path(record.get("worktree") or "")
    if not folder.is_dir():
        raise ReviewError(f"copia dell'unità sparita: {folder}")
    return gates.head_of(folder, str(record.get("branch") or ""))


def _review_text(
    task_id: str, unit: str, round_no: int, verdict: str, head: str, collection: dict, body: str,
) -> str:
    """The file a person reads: what was decided, on which commit, and why or with what to fix."""
    collected = str(collection.get("head") or "")
    lines = [
        f"# Revisione dell'unità {unit} — giro {round_no}",
        "",
        f"- compito: {task_id}",
        f"- unità: {unit}",
        f"- giro: {round_no}",
        f"- verdetto: {verdict}",
        f"- head: {head}",
        "- raccolta: " + (
            f"{collection.get('verdict') or '?'} (head {collected[:12]})" if collected
            else f"{collection.get('verdict') or 'mai raccolta'}"
        ),
        f"- quando: {state.now_iso()}",
        "",
        "## Motivo" if verdict == state.APPROVED else "## Correzioni",
        "",
        body.rstrip(),
        "",
    ]
    return "\n".join(lines)
