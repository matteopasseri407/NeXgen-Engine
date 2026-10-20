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

from pathlib import Path

from nexgen_core.files import write_private_text

from . import supervision_gates as gates
from . import supervision_harness as harness
from . import supervision_state as state
from .supervision_state import SupervisionError

#: The file of one review round; the round starts at 1 and only goes up.
REVIEW_FILE = "45-revisione-{unit}-{round}.md"


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


def pending_round(task_id: str, unit: str) -> int:
    """The last RIMANDA round that never reached the executor, or 0.

    A round is delivered when its `unit_reworked` event exists: that event is
    written by a run in the session and by the tab a lost session opens.
    """
    name = state.slug(unit)
    last = _last_review(task_id, name)
    if not last or str(last.get("verdict") or "") != state.REWORKED:
        return 0
    round_no = int(last.get("round") or 0)
    delivered = any(
        row.get("event") == "unit_reworked" and row.get("unit") == name
        and int(row.get("round") or 0) == round_no
        for row in state.read_events(task_id)
    )
    return 0 if delivered else round_no


def _last_review(task_id: str, name: str) -> dict:
    """The unit's last review event, or an empty row when it was never reviewed."""
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


def resend(task_id: str, unit: str, *, sender=None) -> dict:
    """Deliver again the last round that never reached the executor, without a new number.

    The round file is already written and the journal already knows it: this
    only repeats the delivery, so a busy session or a missing one cannot burn a
    round number or leave the corrections behind.
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
        task_id, name, path, round_no, open_without_session=True,
    )
    last = _last_review(task_id, name)
    return {
        "unit": name, "round": round_no, "path": path, "verdict": state.REWORKED,
        "head": str(last.get("head") or ""), "delivery": delivery,
    }


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
