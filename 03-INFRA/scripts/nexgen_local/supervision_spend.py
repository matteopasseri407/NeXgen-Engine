"""What the harness's own archive says was spent in the last 5 hours, 7 days and 30 days.

One job: sum the costs `opencode.db` already carries into the windows the owner
asked to see, whole or split by the provider that was paid. No budget rule lives
in the engine: the numbers are shown, and what to do about them stays the
owner's decision. A store that cannot be read leaves the window unknown, never zero.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import supervision_costs as costs

#: The windows `sup costi` shows: a stable key and how far back it reaches.
SPEND_WINDOWS = (("5h", 5 * 3600), ("7d", 7 * 86400), ("30d", 30 * 86400))
#: Where the archive keeps one row per session. `session_v2` is the live schema;
#: the old table repeats the same rows, so exactly one of them is ever summed.
SPEND_TABLES = ("session_v2", "session")
#: The key a session's `model` JSON carries its provider under.
_PROVIDER_KEY = "providerID"
#: The provider of a session whose row says nothing: a real row, never a lost one.
UNKNOWN_PROVIDER = "(sconosciuto)"


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


def spend_by_provider(*, db: Path | None = None, now: float | None = None, home: Path | None = None) -> dict:
    """Spend per window and per provider: the same windows, split by who was paid.

    `opencode-go` (the flat subscription) and `opencode` (Zen) are two invoices,
    so a total over both answers the wrong question. The provider rides in the
    session's `model` JSON; a session whose row does not say is counted under
    `UNKNOWN_PROVIDER` instead of being dropped or folded into another lane.

    The windows keep their meaning: a session falls in one by its last update,
    the only instant the archive keeps. A store that cannot be read carries the
    reason in `note` and no providers at all, which is not the same as "nothing
    was spent".
    """
    moment = time.time() if now is None else float(now)
    path = db or costs.opencode_db(home)
    if path is None or not Path(path).is_file():
        return _unreadable_providers(moment, "archivio del harness non trovato")
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return _unreadable_providers(moment, f"archivio non leggibile ({exc})")
    try:
        table = next((name for name in SPEND_TABLES if _has_table(con, name)), "")
        if not table:
            return _unreadable_providers(moment, "archivio senza la tabella delle sessioni")
        rows = list(_provider_rows(con, table, moment - SPEND_WINDOWS[-1][1]))
    except sqlite3.Error as exc:
        return _unreadable_providers(moment, f"archivio non leggibile ({exc})")
    finally:
        con.close()
    return {
        "read_at": costs.now_iso(moment),
        "source": f"opencode.db ({table})",
        "note": "",
        "providers": _group_by_provider(rows, moment),
    }


def _unreadable_providers(moment: float, note: str) -> dict:
    """A reading that did not happen: the reason, and no provider invented."""
    return {"read_at": costs.now_iso(moment), "source": "", "note": note, "providers": {}}


def _provider_rows(con: sqlite3.Connection, table: str, cutoff: float):
    """Every session since `cutoff`, as (last update in seconds, cost, provider).

    The `model` column arrived with the v2 schema and an older archive has no
    such column: asking for it unconditionally would make the whole read fail
    on exactly the machines that have been running longest.
    """
    columns = {str(row[1]) for row in con.execute(f"pragma table_info({table})")}
    model = "model" if "model" in columns else "null"
    for updated, cost, raw in con.execute(
        f"select time_updated, cost, {model} from {table} where time_updated >= ?",
        (int(cutoff * 1000),),
    ):
        yield int(updated) / 1000.0, float(cost or 0.0), _provider_of(raw)


def _provider_of(raw) -> str:
    """The provider id inside a session's `model` JSON, or the unknown marker."""
    if not raw:
        return UNKNOWN_PROVIDER
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return UNKNOWN_PROVIDER
    if not isinstance(parsed, dict):
        return UNKNOWN_PROVIDER
    name = parsed.get(_PROVIDER_KEY)
    return str(name) if isinstance(name, str) and name else UNKNOWN_PROVIDER


def _group_by_provider(rows: list[tuple[float, float, str]], moment: float) -> dict:
    """The three windows per provider, summing the rows that fall inside each."""
    grouped: dict[str, dict] = {}
    for updated, cost, provider in rows:
        windows = grouped.setdefault(
            provider, {key: {"cost_usd": 0.0, "sessions": 0} for key, _ in SPEND_WINDOWS},
        )
        for key, seconds in SPEND_WINDOWS:
            if updated >= moment - seconds:
                windows[key]["cost_usd"] += cost
                windows[key]["sessions"] += 1
    return {
        provider: {key: {"cost_usd": round(value["cost_usd"], 6), "sessions": value["sessions"]}
                   for key, value in windows.items()}
        for provider, windows in grouped.items()
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
