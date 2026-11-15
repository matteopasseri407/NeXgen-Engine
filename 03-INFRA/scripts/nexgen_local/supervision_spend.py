"""What the harness's own archive says was spent in the last 5 hours, 7 days and 30 days.

One job: sum the costs of the harness archive into the windows the owner asked to
see, whole or split by the provider that was paid. No budget rule lives in the
engine: the numbers are shown, and what to do about them stays the owner's
decision. A store that cannot be read leaves the window unknown, never zero.

Where the dollars come from. The archive records a cost per session, and this
module summed that column until now. `ccusage` (MIT) reads the same archive
through its OpenCode adapter, skips the message rows a v2 fork copied from its
parent, and prices a model from its bundled price list when OpenCode stored zero
(a flat subscription records zero, so the column alone reads as "nothing spent").
Its price per session is therefore the number shown, and the archive keeps the
position in time of each session, which is the half ccusage's JSON does not carry.
A session ccusage does not name keeps the archive's own cost, and a machine
without the binary gets exactly the sum this module produced before it existed.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from nexgen_core import ccusage
from nexgen_core.paths import resolve_state_dir

from . import supervision_costs as costs

#: The windows `sup costi` shows: a stable key and how far back it reaches.
SPEND_WINDOWS = (("5h", 5 * 3600), ("7d", 7 * 86400), ("30d", 30 * 86400))
#: Where the archive keeps one row per session. `session_v2` is the live schema;
#: the old table repeats the same rows, so exactly one of them is ever summed.
SPEND_TABLES = ("session_v2", "session")
#: The one view of ccusage's OpenCode source this module reads: a row per session.
#: `--offline` keeps a spend reading off the network, and `--no-color` keeps the
#: answer parseable on a terminal that would otherwise wrap it.
PRICES_COMMAND = ("opencode", "session", "--json", "--offline", "--no-color")
#: Where the last price list waits, under the machine-local state directory.
PRICES_CACHE = ("nexgen", "ccusage-opencode.json")
#: The key a session's `model` JSON carries its provider under.
_PROVIDER_KEY = "providerID"
#: The provider of a session whose row says nothing: a real row, never a lost one.
UNKNOWN_PROVIDER = "(sconosciuto)"


def spend_windows(*, db: Path | None = None, now: float | None = None,
                  home: Path | None = None) -> dict:
    """Spend per window from the harness archive, children included, or why it is unknown.

    Every session carries its own cost in its own row, subagents too, so one sum
    over the window is the whole spend and no walk is needed. A session falls in
    a window by its last update, the only instant the archive keeps. When
    ccusage answers, each session is priced by it instead; when the store is
    missing or unreadable every window is `None` with the reason: an unknown
    spend is never written as zero.

    `home` is the home in play, not necessarily the process's, and it decides
    both which archive is read and where the price list is cached.
    """
    moment = time.time() if now is None else float(now)
    unknown = {key: {"cost_usd": None, "sessions": None} for key, _ in SPEND_WINDOWS}
    path = db or costs.opencode_db(home)
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
        rows = list(_rows(con, table, moment - SPEND_WINDOWS[-1][1]))
    except sqlite3.Error as exc:
        return _unreadable(moment, f"archivio non leggibile ({exc})", unknown)
    finally:
        con.close()
    prices = _prices(Path(path), home=home, moment=moment)
    return {
        "read_at": costs.now_iso(moment),
        "source": _source(table, prices),
        "windows": {key: _window(rows, prices, moment - seconds)
                    for key, seconds in SPEND_WINDOWS},
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
        rows = list(_rows(con, table, moment - SPEND_WINDOWS[-1][1]))
    except sqlite3.Error as exc:
        return _unreadable_providers(moment, f"archivio non leggibile ({exc})")
    finally:
        con.close()
    prices = _prices(Path(path), home=home, moment=moment)
    return {
        "read_at": costs.now_iso(moment),
        "source": _source(table, prices),
        "note": "",
        "providers": _group_by_provider(rows, prices, moment),
    }


def prices_cache_path(home: Path | None = None) -> Path:
    """Where the last price list waits: machine-local state, never the archive's folder."""
    return resolve_state_dir(home).joinpath(*PRICES_CACHE)


def _asked(home: Path | None) -> bool:
    """Whether `quota.yaml` lets this binary be asked at all.

    One binary has one switch, and `ccusage.enabled` reads it: a machine that wrote
    `backends: {ccusage: off}` for the bot's own spend does not want a second reader
    of local transcripts either. `quota.limits_path` owns the file's location, and
    it is reached at the call so this module repeats neither the Vault's layout nor
    the engine's import order.
    """
    from nexgen_core import quota

    return ccusage.enabled(quota.limits_path(home))


def _source(table: str, prices: dict[str, float]) -> str:
    """Which archive table answered, and whether a price list was added to it."""
    return f"opencode.db ({table})" + (" + ccusage" if prices else "")


def _prices(path: Path, *, home: Path | None, moment: float) -> dict[str, float]:
    """What ccusage says each session cost, or an empty map when it cannot answer.

    One answer is kept for `ccusage.CACHE_TTL_SECONDS` and only for the archive it
    was bought from: a run walks every message row and takes seconds, and a list
    bought for another database would price the wrong sessions. A run that started
    and then failed is kept for the same interval, so a broken binary is asked once
    rather than on every command; a binary that is not installed ran nothing and is
    not remembered, so installing it shows on the next command. No price list is
    ever an error here: the archive's own cost answers instead, which is what this
    module did before the binary existed.

    The run starts in the archive's folder, because ccusage resolves a relative
    `OPENCODE_DATA_DIR` against the working directory and a reading must not
    depend on where a command happened to be typed.
    """
    cache = prices_cache_path(home)
    if not _asked(home):
        return {}
    stored = ccusage.cache_read(cache)
    if stored is not None and stored.get("archive") == str(path) \
            and ccusage.cache_is_fresh(stored, moment=moment):
        return _stored_prices(stored.get("prices"))
    answer = ccusage.run(PRICES_COMMAND, cwd=Path(path).parent)
    prices: dict[str, float] = {}
    error = answer.error
    if answer.ok:
        try:
            prices = prices_from(answer.out)
        except ccusage.FormatError:  # nexgen: allow reason="an answer this module does not understand is no price list, so the archive's own cost answers as it did before the binary existed; the failure is remembered for the interval instead of being paid for by every command"
            error = ccusage.FORMAT
    elif error == ccusage.MISSING:
        # A binary that is not installed ran nothing, so there is nothing to keep:
        # installing it later is visible on the next command instead of ten minutes on.
        return prices
    ccusage.cache_write(cache, {"read_at": moment, "archive": str(path),
                                "prices": prices, "error": error})
    return prices


def prices_from(text: str) -> dict[str, float]:
    """The price ccusage reports for each session id, or a `FormatError` naming the row."""
    prices: dict[str, float] = {}
    for index, entry in enumerate(ccusage.document(text, "sessions")):
        row = ccusage.object_row(entry, f"riga {index}", "sessions")
        session = ccusage.require_text(row, "sessionId", f"riga {index}")
        prices[session] = ccusage.require_cost(row, f"sessione {session}")
    return prices


def _stored_prices(value) -> dict[str, float]:
    """The prices a stored answer carries, skipping an entry that is not a cost."""
    if not isinstance(value, dict):
        return {}
    return {str(key): float(cost) for key, cost in value.items()
            if ccusage.is_number(cost) and cost >= 0}


def _unreadable_providers(moment: float, note: str) -> dict:
    """A reading that did not happen: the reason, and no provider invented."""
    return {"read_at": costs.now_iso(moment), "source": "", "note": note, "providers": {}}


def _rows(con: sqlite3.Connection, table: str, cutoff: float):
    """Every session since `cutoff`, as (id, last update in seconds, cost, provider).

    The `model` column arrived with the v2 schema and an older archive has no
    such column: asking for it unconditionally would make the whole read fail
    on exactly the machines that have been running longest.
    """
    columns = {str(row[1]) for row in con.execute(f"pragma table_info({table})")}
    model = "model" if "model" in columns else "null"
    for identifier, updated, cost, raw in con.execute(
        f"select id, time_updated, cost, {model} from {table} where time_updated >= ?",
        (int(cutoff * 1000),),
    ):
        yield str(identifier), int(updated) / 1000.0, float(cost or 0.0), _provider_of(raw)


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


def _group_by_provider(rows: list[tuple[str, float, float, str]],
                       prices: dict[str, float], moment: float) -> dict:
    """The three windows per provider, summing the sessions that fall inside each."""
    grouped: dict[str, dict] = {}
    for identifier, updated, cost, provider in rows:
        windows = grouped.setdefault(
            provider, {key: {"cost_usd": 0.0, "sessions": 0} for key, _ in SPEND_WINDOWS},
        )
        for key, seconds in SPEND_WINDOWS:
            if updated >= moment - seconds:
                windows[key]["cost_usd"] += prices.get(identifier, cost)
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


def _window(rows: list[tuple[str, float, float, str]], prices: dict[str, float],
            cutoff: float) -> dict:
    """How many sessions last moved after `cutoff` and what all of them cost together.

    A session ccusage priced counts at that price, because it is the reader that
    knows which rows a fork copied from its parent; a session it did not name
    keeps the cost the archive recorded.
    """
    inside = [row for row in rows if row[1] >= cutoff]
    return {"cost_usd": round(sum(prices.get(identifier, cost)
                                 for identifier, _updated, cost, _provider in inside), 6),
            "sessions": len(inside)}


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    """Whether this store carries the sessions table `name`."""
    return con.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?", (name,)
    ).fetchone() is not None
