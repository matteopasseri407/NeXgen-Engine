"""What the harness's own archive says was spent in the last 5 hours, 7 days and 30 days.

One job: sum the costs `opencode.db` already carries into the windows the owner
asked to see. No budget rule lives in the engine: the numbers are shown, and
what to do about them stays the owner's decision. A store that cannot be read
leaves the window unknown, never zero.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from . import supervision_costs as costs

#: The windows `sup costi` shows: a stable key and how far back it reaches.
SPEND_WINDOWS = (("5h", 5 * 3600), ("7d", 7 * 86400), ("30d", 30 * 86400))
#: Where the archive keeps one row per session. `session_v2` is the live schema;
#: the old table repeats the same rows, so exactly one of them is ever summed.
SPEND_TABLES = ("session_v2", "session")


def spend_windows(*, db: Path | None = None, now: float | None = None) -> dict:
    """Spend per window from the harness archive, children included, or why it is unknown.

    Every session carries its own cost in its own row, subagents too, so one sum
    over the window is the whole spend and no walk is needed. A session falls in
    a window by its last update, the only instant the archive keeps. When the
    store is missing or unreadable every window is `None` with the reason: an
    unknown spend is never written as zero.
    """
    moment = time.time() if now is None else float(now)
    unknown = {key: {"cost_usd": None, "sessions": None} for key, _ in SPEND_WINDOWS}
    path = db or costs.opencode_db()
    if path is None or not Path(path).is_file():
        return _unreadable(moment, "archivio del harness non trovato", unknown)
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return _unreadable(moment, f"archivio non leggibile ({exc})", unknown)
    try:
        table = next((name for name in SPEND_TABLES if _has_table(con, name)), "")
        if not table:
            return _unreadable(moment, "archivio senza la tabella delle sessioni", unknown)
        found = {key: _window(con, table, moment - seconds) for key, seconds in SPEND_WINDOWS}
    except sqlite3.Error as exc:
        return _unreadable(moment, f"archivio non leggibile ({exc})", unknown)
    finally:
        con.close()
    return {
        "read_at": costs.now_iso(moment),
        "source": f"opencode.db ({table})",
        "windows": found,
        "note": "",
    }


def _unreadable(moment: float, note: str, unknown: dict) -> dict:
    """The shape of a reading that did not happen: the reason instead of three zeros."""
    return {
        "read_at": costs.now_iso(moment),
        "source": "",
        "windows": dict(unknown),
        "note": note,
    }


def _window(con: sqlite3.Connection, table: str, cutoff: float) -> dict:
    """How many sessions last moved after `cutoff` and what all of them cost together."""
    count, total = con.execute(
        f"select count(*), coalesce(sum(cost), 0.0) from {table} where time_updated >= ?",
        (int(cutoff * 1000),),
    ).fetchone()
    return {"cost_usd": round(float(total or 0.0), 6), "sessions": int(count)}


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    """Whether this store carries the sessions table `name`."""
    return con.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?", (name,)
    ).fetchone() is not None
