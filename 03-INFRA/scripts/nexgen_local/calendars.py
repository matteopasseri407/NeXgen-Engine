"""The calendar gate: events the human describes, the engine creates or deletes.

Same contract as mail (see ``compose.py``): propose first on explicit
fields, approve on machine facts, apply only with ``--yes``. The model never
supplies dates or titles here: the human (or a calling agent) names them, the
gate validates them. Audit intent before, outcome after.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from nexgen_core.files import write_private_text

from .config import LaneConfig
from .connectors import ConnectorError
from .connectors import calendar as calendar_conn
from .patch import new_proposal_id, valid_proposal_id
from .tools import ToolError, audit_event


class CalendarError(RuntimeError):
    """The proposal or the application was refused."""


@dataclass
class CalendarProposal:
    id: str
    kind: str  # "create" or "delete"
    calendar_id: str
    summary: str
    start: str
    end: str
    description: str
    location: str
    event_id: str
    created_at: str = ""
    applied_at: str = ""
    done_id: str = ""


def _save(cfg: LaneConfig, proposal: CalendarProposal) -> None:
    target = cfg.calendars_dir / f"{proposal.id}.json"
    write_private_text(target, json.dumps(asdict(proposal), ensure_ascii=False, indent=1))


def _create(cfg: LaneConfig, proposal: CalendarProposal) -> None:
    """Store a new proposal without ever overwriting an existing one (see patch._create)."""
    target = cfg.calendars_dir / f"{proposal.id}.json"
    try:
        write_private_text(target, json.dumps(asdict(proposal), ensure_ascii=False, indent=1), exclusive=True)
    except FileExistsError as exc:
        raise CalendarError(f"collisione id proposta, riprova: {proposal.id}") from exc


def load_proposal(cfg: LaneConfig, proposal_id: str) -> CalendarProposal:
    if not valid_proposal_id(proposal_id or ""):
        raise CalendarError(f"id proposta non valido: {proposal_id}")
    target = cfg.calendars_dir / f"{proposal_id}.json"
    if not target.is_file():
        raise CalendarError(f"proposta inesistente: {proposal_id}")
    return CalendarProposal(**json.loads(target.read_text(encoding="utf-8")))


def list_proposals(cfg: LaneConfig) -> list[CalendarProposal]:
    if not cfg.calendars_dir.is_dir():
        return []
    proposals = []
    for path in sorted(cfg.calendars_dir.glob("*.json"), reverse=True):
        try:
            proposals.append(CalendarProposal(**json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, TypeError, ValueError):
            continue
    return proposals


def _store_new(cfg: LaneConfig, proposal: CalendarProposal) -> CalendarProposal:
    """Assign a unique id and store without overwriting; retry on collision."""
    for _ in range(5):
        proposal.id = new_proposal_id()
        try:
            _create(cfg, proposal)
            return proposal
        except CalendarError as exc:
            if "collisione" not in str(exc):
                raise
    raise CalendarError("collisione id proposta, riprova")


def propose_event(
    cfg: LaneConfig,
    summary: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
) -> CalendarProposal:
    """Stage an event creation on explicit fields; no model involved."""
    summary, start, end = str(summary or "").strip(), str(start or "").strip(), str(end or "").strip()
    if not summary:
        raise CalendarError("titolo mancante")
    if not start or not end:
        raise CalendarError("inizio/fine mancanti (ISO 8601 con timezone)")
    proposal = CalendarProposal(
        id="",
        kind="create",
        calendar_id=str(calendar_id or "primary").strip() or "primary",
        summary=summary,
        start=start,
        end=end,
        description=str(description or ""),
        location=str(location or ""),
        event_id="",
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    _store_new(cfg, proposal)
    audit_event(cfg, "propose_calendar", {"kind": "create", "summary": summary}, ok=True, chars=len(summary))
    return proposal


def propose_delete(cfg: LaneConfig, calendar_id: str = "primary", event_id: str = "") -> CalendarProposal:
    """Stage an event deletion; the event must exist and be shown first."""
    event_id = str(event_id or "").strip()
    if not event_id:
        raise CalendarError("id evento mancante")
    try:
        existing = calendar_conn.get_event(calendar_id, event_id)
    except ConnectorError as exc:
        raise CalendarError(f"evento non leggibile: {exc.refusal}") from exc
    proposal = CalendarProposal(
        id="",
        kind="delete",
        calendar_id=str(calendar_id or "primary").strip() or "primary",
        summary=str(existing.get("summary", "")),
        start="",
        end="",
        description="",
        location="",
        event_id=event_id,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    _store_new(cfg, proposal)
    audit_event(cfg, "propose_calendar", {"kind": "delete", "event": event_id}, ok=True, chars=0)
    return proposal


def format_gate(proposal: CalendarProposal) -> str:
    """The approval screen: machine facts first."""
    lines = [
        f"Proposta {proposal.id} — fatti macchina",
        f"  tipo: {proposal.kind}",
        f"  calendario: {proposal.calendar_id}",
    ]
    if proposal.kind == "create":
        lines.extend(
            [
                f"  titolo: {proposal.summary}",
                f"  inizio: {proposal.start}",
                f"  fine: {proposal.end}",
                f"  luogo: {proposal.location or '(nessuno)'}",
                f"  note: {proposal.description or '(nessuna)'}",
            ]
        )
    else:
        lines.extend(
            [
                f"  evento: {proposal.event_id}",
                f"  titolo: {proposal.summary}",
            ]
        )
    lines.append(f"  Applica con: nexgen-local cal-apply {proposal.id} --yes")
    return "\n".join(lines)


def apply_proposal(cfg: LaneConfig, proposal_id: str, *, yes: bool) -> dict[str, Any]:
    """Execute exactly the approved proposal, once, with explicit --yes."""
    if not yes:
        raise CalendarError("applicazione rifiutata: serve --yes esplicito")
    proposal = load_proposal(cfg, proposal_id)
    if proposal.applied_at:
        raise CalendarError("proposta gia' applicata")
    audit_event(
        cfg,
        "apply_calendar",
        {"proposal": proposal.id, "kind": proposal.kind, "phase": "intent"},
        ok=True,
        chars=0,
    )
    try:
        if proposal.kind == "create":
            done = calendar_conn.create_event(
                proposal.summary,
                proposal.start,
                proposal.end,
                proposal.description,
                proposal.location,
                proposal.calendar_id,
            )
        else:
            # The event must still be there: no phantom deletions.
            calendar_conn.get_event(proposal.calendar_id, proposal.event_id)
            done = calendar_conn.delete_event(proposal.calendar_id, proposal.event_id)
    except ConnectorError as exc:
        audit_event(cfg, "apply_calendar", {"proposal": proposal.id, "phase": "result"}, ok=False, chars=0)
        raise CalendarError(f"applicazione fallita: {exc.refusal}") from exc
    proposal.applied_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    proposal.done_id = str(done.get("id", ""))
    try:
        _save(cfg, proposal)
        audit_event(
            cfg,
            "apply_calendar",
            {"proposal": proposal.id, "phase": "result", "done_id": proposal.done_id},
            ok=True,
            chars=0,
        )
    except (OSError, ToolError) as exc:
        raise CalendarError(f"applicata, ma stato o ricevuta finale non salvati: {exc}") from exc
    return {"id": proposal.id, "applied": True, "done_id": proposal.done_id, "kind": proposal.kind}
