"""How a session's last turn closed: succeeded, or ended with a provider error.

One job: read the harness's own store for the last turn of a session and return
its outcome, the provider's error type and when it happened. The journal says a
delivery started, and a turn that the provider cut off with an error (`failed`,
`provider.content-filter`, zero output tokens) never reaches the journal at all:
only the session knows, and this module is how the engine asks. It reads the
store directly because the export says `failed` too but not which provider
error, and it costs a second process per status line.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import supervision_costs as costs
from .supervision_state import SupervisionError


class TurnError(SupervisionError):
    """The harness store is missing or unreadable: the last turn cannot be named."""


@dataclass(frozen=True)
class Turn:
    """What the session's last turn did, as far as the store can tell."""

    outcome: str = ""
    error_type: str = ""
    error_message: str = ""
    at_ms: int = 0

    @property
    def failed(self) -> bool:
        """The provider ended the turn without an answer, so no work reached the files."""
        return self.outcome == "failed"

    def why(self) -> str:
        """One short phrase: the provider's error type, or the outcome when it has none."""
        return self.error_type or self.outcome or "esito sconosciuto"


def last_turn(session: str, *, db: Path | None = None) -> Turn:
    """The last `idle` message of one session, read from the harness's own store.

    No turn at all is an empty `Turn`, not an error: a session that just opened
    has not finished anything, and accusing it of a failure would be a lie.
    """
    path = db or costs.opencode_db()
    if path is None:
        raise TurnError("archivio del harness non trovato: l'ultimo turno non è leggibile")
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise TurnError(f"archivio del harness non leggibile ({exc})") from exc
    try:
        row = con.execute(
            "select time_created, data from session_message "
            "where session_id = ? and type = 'idle' order by time_created desc limit 1",
            (session,),
        ).fetchone()
    except sqlite3.Error as exc:  # nexgen: allow reason="uno schema senza session_message non dice niente sul turno: il chiamante mostra il fatto come non leggibile"
        raise TurnError(f"ultimo turno non leggibile ({exc})") from exc
    finally:
        con.close()
    if not row:
        return Turn()
    return _from_row(row)


def _from_row(row: tuple) -> Turn:
    """The idle message as a `Turn`; a body that does not parse keeps only its time."""
    at_ms = int(row[0] or 0)
    try:
        data = json.loads(row[1] or "{}")
    except (TypeError, ValueError):  # nexgen: allow reason="un messaggio illeggibile non è un turno fallito: resta senza esito"
        return Turn(at_ms=at_ms)
    if not isinstance(data, dict):
        return Turn(at_ms=at_ms)
    error = data.get("error") or {}
    if not isinstance(error, dict):
        error = {}
    return Turn(
        outcome=str(data.get("outcome") or ""),
        error_type=str(error.get("type") or ""),
        error_message=str(error.get("message") or ""),
        at_ms=at_ms,
    )
