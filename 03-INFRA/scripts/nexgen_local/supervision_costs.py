# nexgen: one-job reason="read, record and aggregate what a harness session cost"
"""What a harness session cost: read its export, its children, and aggregate.

The engine never calls a model and never estimates a price: for an OpenCode
session it reads what the harness itself reports (`opencode session export`),
adds what the sessions the harness opened under it cost, writes one row per
attach to the task's `05-costi.jsonl`, and can sum those rows by task, role and
unit. Subagents are not in the export or in `session list`: they are read from
the harness's own store (`opencode.db`, `parent_id`), and when that store cannot
be read the row says `figlie non esposte` instead of pretending there are none.
A source that cannot be read still leaves a row, with a null cost and the
reason: a missing number is never written as zero.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from nexgen_core.processes import windows_command_argv
from nexgen_core.paths import resolve_home

from . import supervision_state as state
from .supervision_state import SupervisionError

#: How the number was obtained: the only source this phase can read.
SOURCE = "opencode session export"
EXPORT_TIMEOUT_S = 180.0
#: What OpenCode prints: `ses_` and alphanumerics. The shape is narrow on purpose,
#: so nothing a command line reads (`&`, `;`, quotes, spaces) can ever get through.
_SESSION_RE = re.compile(r"ses_[A-Za-z0-9]{1,64}")
#: The shape a harness store names its own sessions with: Codex uses a UUID.
_THREAD_RE = re.compile(r"[A-Za-z0-9_-]{1,200}")
#: What an export says when the session is gone, as opposed to a failed read.
_MISSING_EVIDENCE = re.compile(r"(not found|does not exist|no such session|unknown session|non esiste)", re.IGNORECASE)
#: The harness's own store, and the tables that hold sessions in its two schemas.
SESSION_TABLES = ("session", "session_v2")
CHILDREN_MISSING = "figlie non esposte"


class CostError(SupervisionError):
    """The harness executable is missing, the export failed, or its JSON is unusable."""


@dataclass(frozen=True)
class CollectCost:
    """What one collection measures: this round, this unit, the whole task, and the model.

    `round_usd` is what the round added since its delivery, `unit_usd` what the
    unit added since it started, `task_usd` the whole session (its children
    included) plus the sessions that came before it. A number that cannot be
    read is None, never zero: a missing figure is a fact, a zero is a lie.
    """

    round_usd: float | None = None
    unit_usd: float | None = None
    task_usd: float | None = None
    model: str = ""
    session: str = ""
    stopped: bool = False
    note: str = ""
    #: What the measurable sessions add up to, when one of them has no source.
    task_known_usd: float | None = None
    task_unknown_sessions: int = 0
    #: What a flat plan reports instead of money: the tokens, and the note saying so.
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_total: int | None = None

    @property
    def known(self) -> bool:
        """Whether all three numbers were measured."""
        return None not in (self.round_usd, self.unit_usd, self.task_usd)

    def as_dict(self) -> dict:
        """The shape the journal and the package carry, so a later reader rebuilds it."""
        return {
            "round_usd": self.round_usd, "unit_usd": self.unit_usd, "task_usd": self.task_usd,
            "model": self.model, "session": self.session, "stopped": self.stopped,
            "note": self.note, "task_known_usd": self.task_known_usd,
            "task_unknown_sessions": self.task_unknown_sessions,
            "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_total,
        }


def collect_cost(
    task_id: str, unit: str, *, facts_of=None, rows=None, events=None, harness: str = "opencode",
) -> CollectCost:
    """Price the collection that is happening: the round, the unit, the task.

    The harness store holds a cumulative cost per session, so a share is a
    difference: the round is what grew since the round's own delivery started,
    the unit since its first attach, and the task is the session now plus the
    sessions that ended before it. With no delivery in the journal the last row
    is the baseline, which is what the ledger knows. A harness on a flat plan
    reports no money but does report tokens: they are carried here, so the one
    line the owner reads says tokens instead of a zero that would be a lie.
    """
    name = state.slug(unit)
    rows = rows if rows is not None else state.read_costs(task_id)
    events = events if events is not None else state.read_events(task_id)
    mine = [row for row in rows if row.get("unit") == name]
    session = str(mine[-1].get("session") or "") if mine else state.harness_session(task_id, harness)
    if not session:
        return CollectCost(note="nessuna sessione agganciata")
    measure = facts_of or (lambda value: measure_for(harness, value))
    try:
        facts = measure(session)
    except CostError as exc:
        return CollectCost(session=session, note=str(exc))
    now = getattr(facts, "cost_usd", None)
    model = str(getattr(facts, "model", "") or "")
    tokens = _tokens_of(facts)
    if not isinstance(now, (int, float)):
        return CollectCost(
            session=session, model=model,
            stopped=bool(getattr(facts, "stopped", False)),
            note=str(getattr(facts, "cost_note", "") or "costo non riportato dal harness"),
            **tokens,
        )
    rows_priced = [
        (str(row.get("at") or ""), float(row["cost_usd"]))
        for row in mine if isinstance(row.get("cost_usd"), (int, float))
    ]
    unit_base = rows_priced[0][1] if rows_priced else 0.0
    round_base = _round_base(events, name, rows_priced, unit_base)
    total = _task_total(rows, session, now)
    return CollectCost(
        round_usd=round(max(now - round_base, 0.0), 6),
        unit_usd=round(max(now - unit_base, 0.0), 6),
        task_usd=total.usd,
        model=model,
        session=session,
        stopped=bool(getattr(facts, "stopped", False)),
        task_known_usd=total.known_usd,
        task_unknown_sessions=total.unknown_sessions,
    )


def _tokens_of(facts) -> dict:
    """The token counts a flat-plan harness reports, in the names `CollectCost` carries."""
    return {
        "tokens_in": getattr(facts, "tokens_in", None),
        "tokens_out": getattr(facts, "tokens_out", None),
        "tokens_total": getattr(facts, "tokens_total", None),
    }


def cost_text(cost: CollectCost) -> str:
    """The one line every verb and every file uses for the cost of a collection.

    A round and a unit that were measured are shown even when one historical
    session has no source: the total then says what is known and how many
    sessions are missing, instead of hiding every number.
    """
    if not _any_number(cost):
        missing = f"costo: non misurato ({cost.note or 'dato assente'})"
        tokens = _token_line(cost)
        return f"{missing} · {tokens}" if tokens else missing
    model = f"modello {cost.model}" if cost.model else "modello non dichiarato"
    parts = []
    if cost.round_usd is not None:
        parts.append(f"giro {cost.round_usd:.4f} $")
    if cost.unit_usd is not None:
        parts.append(f"unità {cost.unit_usd:.4f} $")
    parts.append(_task_part(cost))
    return f"costo: {', '.join(parts)} ({model})"


def total_text(cost: CollectCost, unit: str = "") -> str:
    """The task total alone, in the same form, saying where it was measured."""
    model = f"modello {cost.model}" if cost.model else "modello non dichiarato"
    where = f", misurato alla raccolta di {unit}" if unit else ""
    if cost.task_usd is not None:
        return f"compito {cost.task_usd:.4f} $ ({model}{where})"
    if cost.task_known_usd is not None:
        return f"compito {_known_part(cost)} ({model}{where})"
    return f"compito non misurato ({cost.note or 'dato assente'})"


def _token_line(cost: CollectCost) -> str:
    """The tokens a flat plan reports, in the one sentence the owner reads."""
    if cost.tokens_in is None and cost.tokens_out is None and cost.tokens_total is None:
        return ""
    if cost.tokens_total is not None and cost.tokens_in is None and cost.tokens_out is None:
        return f"token totali: {cost.tokens_total}"
    return f"token: {cost.tokens_in or 0} in, {cost.tokens_out or 0} out"


def _any_number(cost: CollectCost) -> bool:
    """Whether any cost in this collection was measured at all."""
    return any(
        value is not None
        for value in (cost.round_usd, cost.unit_usd, cost.task_usd, cost.task_known_usd)
    )


def _task_part(cost: CollectCost) -> str:
    """The task segment of a cost line: whole, known part, or plainly missing."""
    if cost.task_usd is not None:
        return f"compito {cost.task_usd:.4f} $"
    if cost.task_known_usd is not None:
        return f"compito {_known_part(cost)}"
    return "compito non misurato"


def _known_part(cost: CollectCost) -> str:
    """The sum of the measured sessions and how many were not measured."""
    missing = cost.task_unknown_sessions
    word = "sessione non misurata" if missing == 1 else "sessioni non misurate"
    return f"{cost.task_known_usd:.4f} $ noto, {missing} {word}"


def _round_base(events: list[dict], unit: str, rows_priced: list[tuple[str, float]], unit_base: float) -> float:
    """The cost when this round's delivery started, from the row written at its start."""
    started = ""
    for row in events:
        if row.get("event") in DELIVERY_EVENTS and row.get("unit") == unit:
            started = str(row.get("at") or "")
    if started:
        after = [value for at, value in rows_priced if at >= started]
        return after[0] if after else unit_base
    return rows_priced[-1][1] if rows_priced else unit_base


@dataclass(frozen=True)
class _TaskTotal:
    """The task total, and how much of it has no source.

    `usd` is the whole total only when every session was measured; `known_usd`
    is what the measurable sessions add up to, so one unreadable historical
    session does not erase the round and the unit that were just measured.
    """

    usd: float | None
    known_usd: float
    unknown_sessions: int


def _task_total(rows: list[dict], session: str, now: float) -> _TaskTotal:
    """The live session plus the last number of every session that ended before it."""
    latest: dict[str, float | None] = {}
    for row in rows:
        name = str(row.get("session") or "")
        if not name or name == session:
            continue
        cost = row.get("cost_usd")
        latest[name] = float(cost) if isinstance(cost, (int, float)) else None
    unknown = sum(1 for value in latest.values() if value is None)
    known_usd = round(now + sum(value for value in latest.values() if value is not None), 6)
    return _TaskTotal(
        usd=None if unknown else known_usd,
        known_usd=known_usd,
        unknown_sessions=unknown,
    )


#: The events that say work was handed to a session, in the order they mean anything.
DELIVERY_EVENTS = ("unit_started", "unit_reworked")


class SessionMissing(CostError):
    """The harness itself says the session does not exist: deleted, pruned, or never there.

    A timeout or a locked store is not this: it says nothing about the session,
    and treating it as lost opened a second tab on work that was still running.
    """


@dataclass(frozen=True)
class SessionFacts:
    """What one session costs, children included, and how the harness reports it."""

    model: str = ""
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_reasoning: int | None = None
    tokens_cache_read: int | None = None
    tokens_cache_write: int | None = None
    outcome: str = ""
    created_ms: int | None = None
    updated_ms: int | None = None
    idle_ms: int | None = None
    #: Subagents the harness opened under this session, and what they cost.
    children: int | None = None
    children_cost_usd: float | None = None
    children_source: str = ""
    #: What a harness without a cost reports instead: tokens, and why there is no money.
    tokens_total: int | None = None
    cost_note: str = ""
    error_type: str = ""
    error_message: str = ""

    @property
    def duration_s(self) -> float | None:
        """Wall time from the session's creation to its last update."""
        if self.created_ms is None or self.updated_ms is None:
            return None
        return round((self.updated_ms - self.created_ms) / 1000.0, 3)

    @property
    def stopped(self) -> bool:
        """True when the harness itself says the session went idle after its last update."""
        if self.idle_ms is None or self.updated_ms is None:
            return False
        return self.idle_ms >= self.updated_ms


def record(
    task_id: str,
    unit: str,
    role: str,
    session_id: str,
    *,
    harness: str = "opencode",
    window: str = "",
    now: float | None = None,
) -> dict:
    """Read one harness session and append its row to the task's cost ledger.

    A session whose export cannot be read still gets a row: the cost is null
    and ``note`` says why, so the aggregate can say "unknown" without inventing
    a zero.
    """
    folder = state.open_task(task_id)
    chosen = (harness or "opencode").strip().lower() or "opencode"
    session = _session_id(session_id) if chosen in ("opencode", "local") else _thread_id(session_id)
    name = (unit or "").strip()
    if not name:
        raise SupervisionError("unità vuota: indica a quale unità appartiene la sessione")
    row: dict = {
        "at": now_iso(now),
        "task": task_id,
        "unit": name,
        "role": (role or "").strip() or "esecutore",
        "harness": harness,
        "session": session,
        "window": window,
        "source": source_for(harness),
    }
    try:
        facts = measure_for(harness, session)
    except CostError as exc:
        row.update(_empty_measurements())
        row["cost_usd"] = None
        row["note"] = str(exc)
    else:
        row.update(_measurements(facts))
    state.append_jsonl(folder / state.COSTS_FILE, row)
    return row


def measure_for(harness: str, session_id: str) -> SessionFacts:
    """One session as its own harness reports it; no name means the harness the engine grew up with.

    The import is local because the adapters import this module for the facts they
    return: the registry is only asked at call time, when both modules exist. An
    adapter that cannot measure this session raises its own refusal, which becomes
    a `CostError`: the callers already know how to show a number nobody measured.
    """
    name = (harness or "opencode").strip() or "opencode"
    if name in ("opencode", "local"):
        return measure(session_id)
    from . import supervision_adapters as adapters

    try:
        return adapters.get(name).measure(session_id)
    except CostError:
        raise
    except SupervisionError as exc:
        raise CostError(str(exc)) from exc


def measure(session_id: str) -> SessionFacts:
    """One session as the harness reports it now, with the cost of its children added."""
    facts = facts_from_export(export_session(session_id))
    count, child_cost, source = child_sessions(session_id)
    total = facts.cost_usd
    if child_cost is not None:
        total = round((total or 0.0) + child_cost, 6)
    return replace(facts, cost_usd=total, children=count, children_cost_usd=child_cost, children_source=source)


def child_sessions(session_id: str, *, db: Path | None = None) -> tuple[int | None, float | None, str]:
    """How many sessions the harness opened under this one, what they cost, and where that came from.

    `session export` and `session list` do not expose subagent sessions at all:
    the harness keeps them in its own store with a `parent_id`, and that store
    is the only exact source. When it is missing or unreadable the answer says
    `figlie non esposte`; it never says zero.
    """
    path = db or opencode_db()
    if path is None:
        return None, None, f"{CHILDREN_MISSING}: archivio del harness non trovato"
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return None, None, f"{CHILDREN_MISSING}: archivio non leggibile ({exc})"
    try:
        found, child_cost, tables = _walk_children(con, session_id)
    except sqlite3.Error as exc:
        return None, None, f"{CHILDREN_MISSING}: archivio non leggibile ({exc})"
    finally:
        con.close()
    if not tables:
        return None, None, f"{CHILDREN_MISSING}: nessuna tabella delle sessioni"
    return len(found), round(child_cost, 6), f"figlie da opencode.db ({', '.join(tables)})"


def opencode_db(home: Path | None = None) -> Path | None:
    """Where the harness keeps its sessions, or None when the store is not there.

    `home` is the home in play, not necessarily the process's: a caller reading
    a sandbox, or a machine other than the one this process runs on, must not
    be answered with the archive under the invoking user's home.
    """
    base = Path(os.environ.get("XDG_DATA_HOME") or resolve_home(home) / ".local" / "share")
    path = base / "opencode" / "opencode.db"
    return path if path.is_file() else None


def _walk_children(con: sqlite3.Connection, root: str) -> tuple[list[str], float, list[str]]:
    """Every session under `root`, depth first, across both schemas the harness uses."""
    tables = [name for name in SESSION_TABLES if _has_table(con, name)]
    found: list[str] = []
    total = 0.0
    queue = [root]
    while queue:
        parent = queue.pop()
        for table in tables:
            for child, cost in con.execute(f"select id, cost from {table} where parent_id = ?", (parent,)):
                if child in found:
                    continue
                found.append(str(child))
                total += float(cost or 0.0)
                queue.append(str(child))
    return found, total, tables


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("select 1 from sqlite_master where type = 'table' and name = ?", (name,)).fetchone()
    return row is not None


def export_session(session_id: str, *, timeout: float = EXPORT_TIMEOUT_S) -> dict:
    """Run `opencode session export <id>` and return the parsed JSON.

    The executable comes from PATH and goes through the engine's own shim
    resolver: on Windows `opencode` is a `.cmd`, and a bare spawn of one fails.
    """
    session = _session_id(session_id)
    executable = opencode_executable()
    argv = windows_command_argv([executable, "session", "export", session])
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CostError(f"export della sessione non riuscito ({type(exc).__name__})") from exc
    if result.returncode != 0:
        detail = ((result.stderr or "") + (result.stdout or "")).strip()
        if _MISSING_EVIDENCE.search(detail):
            reason = " ".join(detail.splitlines()[-1:])[:200] if detail else f"exit {result.returncode}"
            raise SessionMissing(f"`opencode session export {session}` non trova la sessione: {reason}")
        raise CostError(f"`opencode session export {session}` esce con {result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise CostError("l'export della sessione non è JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
        raise CostError("l'export della sessione non contiene `info`")
    return payload


def facts_from_export(payload: dict) -> SessionFacts:
    """The fields this engine keeps, read from one session export."""
    info = payload.get("info") or {}
    tokens = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    clock = info.get("time") if isinstance(info.get("time"), dict) else {}
    cost = info.get("cost")
    return SessionFacts(
        model=_model_tag(info.get("model")),
        cost_usd=round(float(cost), 6) if isinstance(cost, (int, float)) else None,
        tokens_in=_int_or_none(tokens.get("input")),
        tokens_out=_int_or_none(tokens.get("output")),
        tokens_reasoning=_int_or_none(tokens.get("reasoning")),
        tokens_cache_read=_int_or_none(cache.get("read")),
        tokens_cache_write=_int_or_none(cache.get("write")),
        outcome=str(info.get("outcome") or ""),
        created_ms=_int_or_none(clock.get("created")),
        updated_ms=_int_or_none(clock.get("updated")),
        idle_ms=_int_or_none(clock.get("idle")),
    )


def aggregate(task_id: str) -> dict:
    """Sum a task's cost rows by role and by unit; unknown costs stay counted as unknown.

    Every row carries the session's cumulative cost, so only the last row of each
    session counts: adding them all would charge the same money once per attach.
    """
    rows = state.latest_cost_rows(state.read_costs(task_id))
    by_role: dict[str, dict] = {}
    by_unit: dict[str, dict] = {}
    for row in rows:
        _bucket_for(by_role, str(row.get("role") or ""), row)
        _bucket_for(by_unit, str(row.get("unit") or ""), row)
    total = _empty_bucket()
    for row in rows:
        _add_to_bucket(total, row)
    return {"task": task_id, **total, "by_role": by_role, "by_unit": by_unit}


def _bucket_for(groups: dict[str, dict], name: str, row: dict) -> None:
    groups.setdefault(name, _empty_bucket())
    _add_to_bucket(groups[name], row)


def opencode_executable() -> str:
    """Where the harness's own CLI is, resolved from PATH and never hardcoded."""
    found = shutil.which("opencode")
    if not found:
        raise CostError("`opencode` non è nel PATH: impossibile leggere la sessione")
    return found


def source_for(harness: str) -> str:
    """Where a row's numbers came from, so an aggregate can say which store it read."""
    name = (harness or "opencode").strip() or "opencode"
    if name in ("opencode", "local"):
        return SOURCE
    return f"adattatore {name}"


def _session_id(session_id: str) -> str:
    """The harness session id, or a refusal before it reaches a command line.

    The id is an argument of `opencode session export`, so a `&`, a `;` or a
    quote there would start a second command instead of naming a session. The
    accepted shape is the one OpenCode prints: `ses_` and alphanumerics.
    """
    session = (session_id or "").strip()
    if not _SESSION_RE.fullmatch(session):
        raise CostError(
            f"identificativo di sessione non valido: {session_id!r} "
            "(atteso `ses_` seguito da lettere e cifre)"
        )
    return session


def _thread_id(session_id: str) -> str:
    """A session id of a harness that keeps its own store: its own shape, still one token.

    Codex names a thread with a UUID and reads it from its SQLite index, so the
    `ses_` shape OpenCode prints would refuse every Codex attach. The id is bound
    as a query parameter, and this check only keeps a line break or a shell
    fragment out of the ledger.
    """
    session = (session_id or "").strip()
    if not _THREAD_RE.fullmatch(session):
        raise CostError(f"identificativo di sessione non valido: {session_id!r}")
    return session


def now_iso(now: float | None) -> str:
    moment = datetime.fromtimestamp(now, timezone.utc) if now is not None else datetime.now(timezone.utc)
    return moment.isoformat(timespec="seconds")


def _model_tag(model) -> str:
    """`provider/model#variant`, the shape the rest of the engine prints."""
    if isinstance(model, str):
        return model
    if not isinstance(model, dict):
        return ""
    provider = str(model.get("providerID") or "")
    name = str(model.get("id") or "")
    tag = f"{provider}/{name}" if provider and name else (name or provider)
    variant = str(model.get("variant") or "")
    return f"{tag}#{variant}" if tag and variant else tag


def _int_or_none(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _measurements(facts: SessionFacts) -> dict:
    return {
        "model": facts.model,
        "outcome": facts.outcome,
        "duration_s": facts.duration_s,
        "tokens_in": facts.tokens_in,
        "tokens_out": facts.tokens_out,
        "tokens_reasoning": facts.tokens_reasoning,
        "tokens_cache_read": facts.tokens_cache_read,
        "tokens_cache_write": facts.tokens_cache_write,
        "cost_usd": facts.cost_usd,
        "stopped": facts.stopped,
        "children": facts.children,
        "children_cost_usd": facts.children_cost_usd,
        "children_source": facts.children_source,
        "tokens_total": facts.tokens_total,
    }


def _empty_measurements() -> dict:
    return {
        "model": "",
        "outcome": "",
        "duration_s": None,
        "tokens_in": None,
        "tokens_out": None,
        "tokens_reasoning": None,
        "tokens_cache_read": None,
        "tokens_cache_write": None,
        "stopped": False,
        "children": None,
        "children_cost_usd": None,
        "children_source": "",
        "tokens_total": None,
    }


def _empty_bucket() -> dict:
    return {"rows": 0, "cost_usd": None, "unknown_rows": 0, "tokens_in": 0, "tokens_out": 0}


def _add_to_bucket(bucket: dict, row: dict) -> None:
    """One row in: sum the cost when it exists, count it as unknown otherwise."""
    bucket["rows"] += 1
    cost = row.get("cost_usd")
    if isinstance(cost, (int, float)):
        bucket["cost_usd"] = round((bucket["cost_usd"] or 0.0) + float(cost), 6)
    else:
        bucket["unknown_rows"] += 1
    for field in ("tokens_in", "tokens_out"):
        value = row.get(field)
        if isinstance(value, (int, float)):
            bucket[field] += int(value)
