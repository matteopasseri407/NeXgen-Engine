"""The `sup permessi` verb: what an executor waits on, and the answers of the profile.

One job: list the permission requests the task's session is waiting on and, when
asked, answer them one at a time, writing each answer to the journal. The profile
already decided what the executor may do; a request that still appears is a
question the profile did not pre-answer, so `--per-profilo` refuses it instead of
granting it. Nothing here decides on its own: every answer is named on the command
line, and the engine never signs a blank cheque to a shell.
"""
from __future__ import annotations

import argparse
import sys

from .. import supervision_permissions as permissions
from .. import supervision_state as state
from ..supervision_state import SupervisionError

#: Where a request carries its own identifier, in the order the harness may name it.
ID_KEYS = ("id", "requestID", "request_id", "requestId", "ID")


def register(sup_sub) -> None:
    """The permission verb: list what the session waits on, or answer one request at a time."""
    verb = sup_sub.add_parser(
        "permessi", help="le richieste di permesso della sessione: elencale, o rispondi secondo il profilo",
    )
    verb.add_argument("id", help="id del compito")
    how = verb.add_mutually_exclusive_group()
    how.add_argument("--allow", action="append", default=[], metavar="RICHIESTA",
                     help="concedi una volta questa richiesta (ripetibile)")
    how.add_argument("--always", action="append", default=[], metavar="RICHIESTA",
                     help="concedi e salva questa richiesta (ripetibile)")
    how.add_argument("--reject", action="append", default=[], metavar="RICHIESTA",
                     help="rifiuta questa richiesta (ripetibile)")
    how.add_argument("--per-profilo", action="store_true",
                     help="rifiuta ogni richiesta in attesa: chiede qualcosa fuori dal profilo scritto")
    verb.set_defaults(func=cmd_sup_permessi)


def cmd_sup_permessi(args: argparse.Namespace) -> int:
    """List the pending requests, or reply to the ones named, and journal every answer."""
    try:
        session = _session(args.id)
        if args.per_profilo:
            return _refuse_all(args.id, session)
        if args.allow or args.always or args.reject:
            return _answer_named(args.id, session, args)
        return _list(args.id, session)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1


def _session(task_id: str) -> str:
    """The session this task's executor runs in; a task nobody attached has none to ask."""
    session = state.harness_session(task_id, "opencode")
    if not session:
        raise SupervisionError(
            f"compito {task_id}: nessuna sessione harness agganciata, quindi nessuna richiesta da leggere"
        )
    return session


def _list(task_id: str, session: str) -> int:
    """Print what the session waits on and how to answer it; asking changes nothing."""
    pending = permissions.pending(session)
    if not pending:
        print(f"sessione {session}: nessuna richiesta in attesa")
        return 0
    print(f"sessione {session} · {_count(len(pending))} in attesa")
    for number, item in enumerate(pending, start=1):
        print(f"  {number}. {_id_of(item) or '(identificativo non leggibile)'} · {permissions.summary(item)}")
    print(f"rispondi: `sup permessi {task_id} --allow <richiesta>` "
          "(o `--always`, o `--reject`; la stessa opzione accetta più identificativi)")
    print(f"oppure: `sup permessi {task_id} --per-profilo` rifiuta tutto ciò che il profilo non ha deciso")
    return 0


def _answer_named(task_id: str, session: str, args: argparse.Namespace) -> int:
    """Reply once per identifier named, in the order the owner wrote them."""
    decisions = (("once", args.allow), ("always", args.always), ("reject", args.reject))
    failed = 0
    for decision, identifiers in decisions:
        for request_id in identifiers:
            if not _reply(task_id, session, request_id, decision):
                failed += 1
    return 1 if failed else 0


def _refuse_all(task_id: str, session: str) -> int:
    """Refuse every pending request: the profile decided, and this one was outside it."""
    pending = permissions.pending(session)
    if not pending:
        print(f"sessione {session}: nessuna richiesta in attesa")
        return 0
    failed = 0
    for item in pending:
        request_id = _id_of(item)
        if not request_id:
            print(f"richiesta non rispondibile, identificativo assente: {permissions.summary(item)}")
            failed += 1
            continue
        if not _reply(task_id, session, request_id, "reject", reason=permissions.PROFILE_REFUSAL):
            failed += 1
    return 1 if failed else 0


def _reply(task_id: str, session: str, request_id: str, decision: str, reason: str = "") -> bool:
    """One answer to the harness, then the journal row that says what was decided and why."""
    label = _decision_text(decision)
    try:
        permissions.answer(session, request_id, decision)
    except SupervisionError as exc:
        print(f"richiesta {request_id}: {label} non riuscita ({exc})", file=sys.stderr)
        return False
    row = state.append_event(
        task_id, "permission_answered", session=session, unit=_unit_of(task_id, session),
        request=request_id, decision=decision, reason=reason,
    )
    print(f"richiesta {request_id}: {label}" + (f" · {reason}" if reason else "")
          + f" · diario {row['at']}")
    return True


def _unit_of(task_id: str, session: str) -> str:
    """The unit this session belongs to, so the journal row lands beside its work."""
    for record in state.status(task_id)["units"]:
        if str(record.get("session") or "") == session:
            return str(record["unit"])
    return ""


def _id_of(item: dict) -> str:
    """The request's own identifier, if the payload carries one in a name we know."""
    for key in ID_KEYS:
        value = item.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _decision_text(decision: str) -> str:
    """The word the owner reads for what the harness was told."""
    return {
        "once": "concessa una volta",
        "always": "concessa e salvata",
        "reject": "rifiutata",
    }.get(decision, decision)


def _count(how_many: int) -> str:
    """One request, two requests: the owner reads these lines."""
    return "1 richiesta" if how_many == 1 else f"{how_many} richieste"
