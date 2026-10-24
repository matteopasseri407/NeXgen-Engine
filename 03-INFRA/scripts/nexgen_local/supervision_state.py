"""The folder of a supervised task: objective, journal, status and templates.

A supervised job outlives the process that starts it: the supervisor reads the
objective and writes the briefs, the executor writes its report, and the engine
keeps one append-only journal of what happened plus one row per cost event.
This module owns those files and reads them back; it starts no process and
measures nothing, so it is testable without a window or a harness.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from nexgen_core.files import secure_artifact, write_private_text

from .config import lane_state_dir
from .supervision_templates import brief_template, report_template

#: Human-readable task files, one job each.
OBJECTIVE_FILE = "00-obiettivo.md"
JOURNAL_FILE = "01-diario.jsonl"
COSTS_FILE = "05-costi.jsonl"
BRIEF_TEMPLATE = "20-brief-MODELLO.md"
REPORT_TEMPLATE = "30-resoconto-MODELLO.md"

#: Longest slug kept in a task id: the timestamp already names the moment.
MAX_SLUG = 40
_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")


class SupervisionError(RuntimeError):
    """A task that does not exist, a folder that cannot hold its files, bad input."""


def _root() -> Path:
    """Where every task folder lives: the lane's own state, never the vault."""
    return lane_state_dir() / "supervision"


def slug(text: str, limit: int = MAX_SLUG) -> str:
    """`text` as a folder-safe tail: accents folded, everything else dashed."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return cleaned[:limit].strip("-") or "compito"


def new_id(objective: str, *, now: float | None = None) -> str:
    """The task id: the moment it started plus the objective's own slug."""
    moment = time.localtime(now) if now is not None else time.localtime()
    return f"{time.strftime('%Y%m%d-%H%M%S', moment)}-{slug(objective)}"


def task_dir(task_id: str) -> Path:
    """The folder of `task_id`, refused when the name could escape the root."""
    name = (task_id or "").strip()
    if not _TASK_ID_RE.fullmatch(name):
        raise SupervisionError(f"id del compito non valido: {task_id!r}")
    return _root() / name


def open_task(task_id: str) -> Path:
    """The folder of an existing task; a missing one is an error, not an empty state."""
    folder = task_dir(task_id)
    if not folder.is_dir():
        raise SupervisionError(
            f"compito inesistente: {task_id} (crealo con `sup start \"<obiettivo>\"`)"
        )
    return folder


def append_jsonl(path: Path, row: dict) -> None:
    """One compact JSON line, appended and closed: a journal is never rewritten."""
    secure_artifact(path.parent)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    """Every row of a JSONL file; a row that does not parse names itself loudly."""
    if not path.is_file():
        return []
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError as exc:
            raise SupervisionError(f"{path.name} riga {number}: JSON non leggibile") from exc
        if not isinstance(parsed, dict):
            raise SupervisionError(f"{path.name} riga {number}: attesa una riga JSON, non {type(parsed).__name__}")
        rows.append(parsed)
    return rows


def append_event(task_id: str, event: str, **fields) -> dict:
    """Record one event on the task's journal and return the written row."""
    folder = open_task(task_id)
    row: dict = {"at": now_iso(), "event": event, "task": task_id}
    row.update({key: value for key, value in fields.items() if value is not None})
    append_jsonl(folder / JOURNAL_FILE, row)
    return row


def read_events(task_id: str) -> list[dict]:
    """The journal of a task, oldest first."""
    return read_jsonl(open_task(task_id) / JOURNAL_FILE)


def read_costs(task_id: str) -> list[dict]:
    """The cost rows of a task, oldest first."""
    return read_jsonl(open_task(task_id) / COSTS_FILE)


def start(objective: str, *, now: float | None = None) -> dict:
    """Create the task folder with its objective, empty templates and journal.

    The id is a timestamp plus a slug of the objective, so two tasks started in
    the same second still get their own folder.
    """
    text = (objective or "").strip()
    if not text:
        raise SupervisionError("obiettivo vuoto: `sup start \"<obiettivo>\"`")
    folder = _fresh_folder(new_id(text, now=now))
    secure_artifact(folder)
    write_private_text(folder / OBJECTIVE_FILE, _objective_text(text, folder.name), exclusive=True)
    write_private_text(folder / BRIEF_TEMPLATE, brief_template(), exclusive=True)
    write_private_text(folder / REPORT_TEMPLATE, report_template(), exclusive=True)
    write_private_text(folder / JOURNAL_FILE, "", exclusive=True)
    append_event(folder.name, "started", objective=text)
    return {"id": folder.name, "path": folder, "objective": text}


def list_tasks() -> list[dict]:
    """Every task folder, oldest first, with the objective or why it cannot be read.

    One damaged journal is a fact about that task, not a reason to hide the
    others: the listing keeps every folder and says which one is unreadable.
    """
    root = _root()
    if not root.is_dir():
        return []
    tasks = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            text, error = objective(folder.name), ""
        except SupervisionError as exc:
            text, error = "", str(exc)
        tasks.append({"id": folder.name, "path": folder, "objective": text, "error": error})
    return tasks


def objective(task_id: str) -> str:
    """The objective in one line: the journal first, the readable file second."""
    folder = open_task(task_id)
    for row in read_events(task_id):
        if row.get("event") == "started" and row.get("objective"):
            return str(row["objective"])
    return _objective_from_file(folder / OBJECTIVE_FILE)


def status(task_id: str) -> dict:
    """What the engine knows about a task right now: units, sessions, last event, costs."""
    folder = open_task(task_id)
    events = read_events(task_id)
    rows = read_costs(task_id)
    snapshots = latest_cost_rows(rows)
    known = [
        float(row["cost_usd"]) for row in snapshots
        if isinstance(row.get("cost_usd"), (int, float))
    ]
    units = _units_from_events(events)
    for record in units:
        record["cost_usd"] = _unit_cost(snapshots, record["unit"])
    last_cost: dict = {}
    for row in events:
        if row.get("event") == "unit_collected" and row.get("cost"):
            last_cost = {**dict(row["cost"]), "unit": str(row.get("unit") or "")}
    return {
        "id": folder.name,
        "path": folder,
        "objective": objective(task_id),
        "events": len(events),
        "units": units,
        "sessions": _sessions_from_events(events),
        "last_event": events[-1] if events else None,
        "last_cost": last_cost,
        "cost_rows": len(rows),
        "cost_total_usd": round(sum(known), 6) if known else None,
        "cost_unknown_rows": len(snapshots) - len(known),
    }


def tasks_lines() -> list[str]:
    """One line per task folder, for `sup status` with no id; a folder nobody can read says so."""
    tasks = list_tasks()
    if not tasks:
        return ['nessun compito: `sup start "<obiettivo>"`']
    lines: list[str] = []
    for task in tasks:
        if task.get("error"):
            lines.append(f"{task['id']}  (illeggibile: {task['error']})")
        else:
            lines.append(f"{task['id']}  {task['objective'] or '(obiettivo non leggibile)'}")
    return lines


def unit_record(task_id: str, unit: str) -> dict:
    """One unit's latest state as the journal describes it; a unit nobody created is an error."""
    name = slug(unit)
    for record in _units_from_events(read_events(task_id)):
        if record["unit"] == name:
            return record
    raise SupervisionError(f"unità sconosciuta: {unit} (creala con `sup unit ...`)")


def harness_session(task_id: str, harness: str = "opencode") -> str:
    """The session this harness already owns on the task, newest first; empty when it has none.

    One harness, one session per task, and the session knows the folder it was
    born in: `harness_session_record` carries both, because OpenCode and Codex
    bind a session to its directory and reusing one after its copy moved fails
    inside the harness. A legacy row written before the folder was recorded has
    an empty folder and stays reusable.
    """
    record = harness_session_record(task_id, harness)
    return record["session"] if record else ""


def harness_session_record(task_id: str, harness: str = "opencode") -> dict | None:
    """The session this harness owns and the folder it was born in, or None.

    A session the harness no longer has is written as `harness_session_lost`,
    and from that line on the task has none: the next unit opens the one new tab
    instead of queueing on a dead session.
    """
    found: dict | None = None
    for row in read_events(task_id):
        if row.get("event") not in (*SESSION_EVENTS, LOST_EVENT):
            continue
        if str(row.get("harness") or "opencode") != harness:
            continue
        if row.get("event") == LOST_EVENT:
            found = None
        elif row.get("session"):
            found = {
                "session": str(row["session"]),
                "folder": str(row.get("folder") or ""),
            }
    return found


def latest_cost_rows(rows: list[dict]) -> list[dict]:
    """One row per session: the last, because each row carries that session's cumulative cost.

    Summing every row counted the same money again at every attach; the last
    snapshot is what the session actually cost. A row nobody tied to a session
    cannot be grouped with another, so it stands for itself.
    """
    by_session: dict[str, dict] = {}
    order: list[str] = []
    loose: list[dict] = []
    for row in rows:
        name = str(row.get("session") or "")
        if not name:
            loose.append(row)
            continue
        if name not in by_session:
            order.append(name)
        by_session[name] = row
    return [by_session[name] for name in order] + loose


def _unit_cost(rows: list[dict], unit: str) -> float | None:
    """What this unit's attached sessions cost, or None when no row carries a number."""
    known = [
        float(row["cost_usd"]) for row in rows
        if row.get("unit") == unit and isinstance(row.get("cost_usd"), (int, float))
    ]
    return round(sum(known), 6) if known else None


def now_iso() -> str:
    """The instant a journal row carries, in UTC and seconds, so rows sort as text."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fresh_folder(task_id: str) -> Path:
    """The first free folder for `task_id`, so a same-second start never collides."""
    root = _root()
    secure_artifact(root)
    folder = root / task_id
    suffix = 1
    while folder.exists():
        suffix += 1
        folder = root / f"{task_id}-{suffix}"
    return folder


def _objective_from_file(path: Path) -> str:
    """The first line of prose after the title, for a task whose journal is gone."""
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        text = line.strip()
        if text and not text.startswith(("#", "-", "*")):
            return text
    return ""


#: How far a unit has gone, from the event that says it.
UNIT_EVENTS = {
    "unit_created": "creata",
    "unit_queued": "in coda",
    "unit_started": "in corso",
    "attached": "in corso",
    "unit_reworked": "in corso",
    "unit_stalled": "in stallo",
    "unit_resumed": "in corso",
    "unit_rebased": "in corso",
    "unit_model_refused": "rifiutata",
    "unit_collected": "raccolta",
    "unit_reviewed": "revisione",
    "unit_publish_failed": "revisione",
    "unit_published": "pubblicata",
    "unit_retired": "ritirata",
}

#: What a review round can say. The words the supervisor writes on the file.
APPROVED = "APPROVA"
REWORKED = "RIMANDA"

#: Events that say which harness session a task owns, and the one that says it is gone.
SESSION_EVENTS = ("attached", "unit_started", "unit_queued", "unit_reworked")
LOST_EVENT = "harness_session_lost"


def _units_from_events(events: list[dict]) -> list[dict]:
    """One entry per unit, folded from every event that describes it.

    A unit starts with `unit_created` (copy, branch, scope), gains its harness
    session with `attached`, is judged by `unit_collected`, decided by
    `unit_reviewed` and, when approved and pushed, closed by `unit_published`.
    """
    units: dict[str, dict] = {}
    for row in events:
        event = str(row.get("event") or "")
        unit = str(row.get("unit") or "")
        if event not in UNIT_EVENTS or not unit:
            continue
        record = units.setdefault(unit, _blank_unit(unit))
        if not (event == "unit_retired" and record.get("published")):
            # A published unit stays published after its copy goes: the retirement
            # is a cleanup fact in `retired`, not a different outcome.
            record["state"] = UNIT_EVENTS[event]
        record["at"] = str(row.get("at") or record["at"])
        _fold_event(record, row, event)
    return list(units.values())


def _blank_unit(unit: str) -> dict:
    """A unit nobody described yet: the fields every later event may fill."""
    return {
        "unit": unit, "role": "", "harness": "", "session": "", "at": "",
        "state": "", "verdict": "", "collected_head": "", "review_round": 0,
        "approved": False, "approved_head": "", "published": [], "model": "",
        "stalled_minutes": 0.0, "stall_threshold": 0.0, "repo": "", "base_ref": "",
        "cost": {}, "folder": "", "retired": False, "publish_conflicts": [],
    }


def _fold_creation(record: dict, row: dict) -> None:
    """What `unit_created` says: the copy, its branch, the base it started from and the scope it may touch."""
    record.update({
        "role": "esecutore",
        "repo": str(row.get("repo") or ""),
        "base_ref": str(row.get("base_ref") or ""),
        "worktree": str(row.get("worktree") or ""),
        "branch": str(row.get("branch") or ""),
        "base": str(row.get("base") or ""),
        "allow": [str(item) for item in row.get("allow") or []],
        "verify": str(row.get("verify") or ""),
        "remotes": dict(row.get("remotes") or {}),
        "remote_urls": dict(row.get("remote_urls") or {}),
        "remote_pushurls": dict(row.get("remote_pushurls") or {}),
        "config_hashes": dict(row.get("config_hashes") or {}),
        "url_override_keys": [str(key) for key in row.get("url_override_keys") or []],
    })


def _fold_event(record: dict, row: dict, event: str) -> None:
    """What one journal row says about the unit, folded into its entry."""
    if event == "unit_created":
        _fold_creation(record, row)
    elif event in ("unit_started", "unit_queued"):
        record.update({
            "session": str(row.get("session") or record["session"]),
            "harness": str(row.get("harness") or record["harness"]),
            "folder": str(row.get("folder") or record["folder"]),
            "mode": str(row.get("mode") or ""),
            "reason": str(row.get("reason") or ""),
        })
    elif event == "attached":
        record.update({
            "session": str(row.get("session") or ""),
            "role": str(row.get("role") or record["role"]),
            "harness": str(row.get("harness") or record["harness"]),
            "folder": str(row.get("folder") or record["folder"]),
        })
    elif event == "unit_collected":
        record["verdict"] = str(row.get("verdict") or "")
        record["collected_head"] = str(row.get("head") or "")
        record["cost"] = dict(row.get("cost") or {})
    elif event == "unit_reviewed":
        record["review_round"] = int(row.get("round") or 0)
        record["approved"] = str(row.get("verdict") or "") == APPROVED
        record["approved_head"] = str(row.get("head") or "") if record["approved"] else ""
        record["review_reason"] = str(row.get("reason") or "")
    elif event == "unit_reworked":
        record["review_round"] = int(row.get("round") or record["review_round"])
        record["approved"] = False
        record["approved_head"] = ""
    elif event in ("unit_model_refused", "unit_stalled", "unit_resumed", "unit_rebased"):
        _fold_progress(record, row, event)
    elif event == "unit_published":
        record["published"] = [str(item) for item in row.get("commits") or []]
        record["published_head"] = str(row.get("head") or "")
        record["published_branch"] = str(row.get("branch") or "")
        record["published_pushed"] = bool(row.get("pushed"))
        if not row.get("pushed"):
            record["state"] = "pubblicata in locale"
    elif event == "unit_publish_failed":
        record["publish_conflicts"] = [str(item) for item in row.get("conflicts") or []]
        record["published"] = []
    elif event == "unit_retired":
        record["retired"] = True
        record["retired_why"] = str(row.get("why") or "")
        record["archive"] = str(row.get("archive") or "")
        record["kept_head"] = str(row.get("kept_head") or "")


def _fold_progress(record: dict, row: dict, event: str) -> None:
    """What the model refusal, the stall and the rebase rows say about a unit's work."""
    if event == "unit_model_refused":
        record.update({
            "model": str(row.get("model") or ""),
            "allowed_models": [str(item) for item in row.get("allowed") or []],
            "reason": str(row.get("reason") or ""),
        })
    elif event == "unit_stalled":
        record["stalled_minutes"] = float(row.get("minutes") or 0.0)
        record["stall_threshold"] = float(row.get("threshold") or 0.0)
    elif event == "unit_resumed":
        record["stalled_minutes"] = 0.0
        record["stall_threshold"] = 0.0
    elif event == "unit_rebased":
        record.update({
            "base": str(row.get("base") or record["base"]),
            "head": str(row.get("head") or ""),
            "config_hashes": dict(row.get("config_hashes") or {}),
            "url_override_keys": [str(key) for key in row.get("url_override_keys") or []],
        })


def _sessions_from_events(events: list[dict]) -> list[str]:
    """Every harness session ever attached, in the order it first appeared."""
    seen: list[str] = []
    for row in events:
        session = str(row.get("session") or "")
        if row.get("event") == "attached" and session and session not in seen:
            seen.append(session)
    return seen


def _objective_text(text: str, task_id: str) -> str:
    return (
        "# Obiettivo\n\n"
        f"{text}\n\n"
        f"- id: {task_id}\n"
        f"- creato: {now_iso()}\n"
    )
