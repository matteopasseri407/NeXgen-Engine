"""What a harness session cost: read its export, append the row, aggregate.

The engine never calls a model and never estimates a price: for an OpenCode
session it reads what the harness itself reports (`opencode session export`),
writes one row per attach to the task's `05-costi.jsonl`, and can sum those
rows by task, role and unit.
A source that cannot be read still leaves a row, with a null cost and the
reason: a missing number is never written as zero.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from nexgen_core.processes import windows_command_argv

from . import supervision_state as state
from .supervision_state import SupervisionError

#: How the number was obtained: the only source this phase can read.
SOURCE = "opencode session export"
EXPORT_TIMEOUT_S = 180.0
#: What OpenCode prints: `ses_` and alphanumerics. The shape is narrow on purpose,
#: so nothing a command line reads (`&`, `;`, quotes, spaces) can ever get through.
_SESSION_RE = re.compile(r"ses_[A-Za-z0-9]{1,64}")


class CostError(SupervisionError):
    """The harness executable is missing, the export failed, or its JSON is unusable."""


@dataclass(frozen=True)
class SessionFacts:
    """What one session export says about its model, tokens, time and cost."""

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
    session = _session_id(session_id)
    name = (unit or "").strip()
    if not name:
        raise SupervisionError("unità vuota: indica a quale unità appartiene la sessione")
    row: dict = {
        "at": _now_iso(now),
        "task": task_id,
        "unit": name,
        "role": (role or "").strip() or "esecutore",
        "harness": harness,
        "session": session,
        "window": window,
        "source": SOURCE,
    }
    try:
        facts = facts_from_export(export_session(session))
    except CostError as exc:
        row.update(_empty_measurements())
        row["cost_usd"] = None
        row["note"] = str(exc)
    else:
        row.update(_measurements(facts))
    state.append_jsonl(folder / state.COSTS_FILE, row)
    return row


def export_session(session_id: str, *, timeout: float = EXPORT_TIMEOUT_S) -> dict:
    """Run `opencode session export <id>` and return the parsed JSON.

    The executable comes from PATH and goes through the engine's own shim
    resolver: on Windows `opencode` is a `.cmd`, and a bare spawn of one fails.
    """
    session = _session_id(session_id)
    executable = _opencode_executable()
    argv = windows_command_argv([executable, "session", "export", session])
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CostError(f"export della sessione non riuscito ({type(exc).__name__})") from exc
    if result.returncode != 0:
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
    """Sum a task's cost rows by role and by unit; unknown costs stay counted as unknown."""
    rows = state.read_costs(task_id)
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


def _opencode_executable() -> str:
    found = shutil.which("opencode")
    if not found:
        raise CostError("`opencode` non è nel PATH: impossibile leggere la sessione")
    return found


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


def _now_iso(now: float | None) -> str:
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
