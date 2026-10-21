"""How a unit and a task read in `sup status`: the same fields, the same cost sentence.

One job: turn the dicts `state.status` returns into the lines the verbs print.
A unit's cost is the one its last collection measured, and the task's is that
same measurement, so the owner reads the same sentence in `sup collect` and in
`sup status`. A number nobody measured is said in words, never written as zero.
"""
from __future__ import annotations

from . import supervision_costs as costs

#: The fields a recorded collection cost carries, in the order it is written.
COST_FIELDS = ("round_usd", "unit_usd", "task_usd", "model", "session", "stopped", "note")


def unit_line(unit: dict) -> str:
    """One unit as the owner reads it: who ran it, how far it got, the review, the cost."""
    parts = [f"unità {unit.get('unit', '')}"]
    if unit.get("role"):
        parts.append(str(unit["role"]))
    session = f"{unit.get('harness', '')} {unit.get('session', '')}".strip()
    if session:
        parts.append(session)
    if unit.get("state"):
        parts.append(str(unit["state"]))
    if unit.get("verdict"):
        parts.append(str(unit["verdict"]))
    parts.append(f"giro {unit.get('review_round') or 0}")
    parts.append("approvata" if unit.get("approved") else "non approvata")
    if unit.get("published"):
        head = str(unit.get("published_head") or "")[:8]
        where = str(unit.get("published_branch") or "")
        pushed = "spinta" if unit.get("published_pushed") else "solo locale"
        parts.append(f"pubblicata {head} su {where} ({pushed})")
    parts.append(unit_cost(unit))
    return " · ".join(parts)


def unit_cost(unit: dict) -> str:
    """The cost sentence of this unit's last collection, or that it was never measured."""
    recorded = _as_cost(unit.get("cost") or {})
    if recorded is None:
        return "costo: non misurato (nessuna raccolta)"
    return costs.cost_text(recorded)


def task_line(data: dict) -> str:
    """The task total in the same form, from the last collection that measured it.

    Before any collection there is no measured total, so the line says what the
    ledger has and what it could not read: an unknown number is named, never
    written as zero.
    """
    raw = data.get("last_cost") or {}
    recorded = _as_cost(raw)
    if recorded is not None:
        return "costi: " + costs.total_text(recorded, str(raw.get("unit") or ""))
    total = data.get("cost_total_usd")
    rows = data.get("cost_rows") or 0
    unknown = data.get("cost_unknown_rows") or 0
    if not rows:
        return "costi: compito non misurato (nessuna sessione agganciata)"
    if total is None:
        return f"costi: compito non disponibile ({row_count(rows)}, {unknown} senza fonte)"
    missing = f", {unknown} senza fonte" if unknown else ""
    return f"costi: compito {total:.4f} $ ({row_count(rows)}{missing})"


def row_count(count: int) -> str:
    """One row, two rows: the owner reads these lines."""
    return f"{count} riga" if count == 1 else f"{count} righe"


def _as_cost(raw: dict) -> costs.CollectCost | None:
    """The cost a collection recorded, as a `CollectCost`, or None when it recorded none."""
    if not raw:
        return None
    return costs.CollectCost(**{key: raw.get(key) for key in COST_FIELDS})
