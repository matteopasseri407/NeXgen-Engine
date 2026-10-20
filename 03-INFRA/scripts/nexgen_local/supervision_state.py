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
    row: dict = {"at": _now_iso(), "event": event, "task": task_id}
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
    write_private_text(folder / BRIEF_TEMPLATE, _brief_template(), exclusive=True)
    write_private_text(folder / REPORT_TEMPLATE, _report_template(), exclusive=True)
    write_private_text(folder / JOURNAL_FILE, "", exclusive=True)
    append_event(folder.name, "started", objective=text)
    return {"id": folder.name, "path": folder, "objective": text}


def list_tasks() -> list[dict]:
    """Every task folder, oldest first, with the objective when it can be read."""
    root = _root()
    if not root.is_dir():
        return []
    tasks = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        tasks.append({"id": folder.name, "path": folder, "objective": objective(folder.name)})
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
    known = [
        float(row["cost_usd"]) for row in rows
        if isinstance(row.get("cost_usd"), (int, float))
    ]
    return {
        "id": folder.name,
        "path": folder,
        "objective": objective(task_id),
        "events": len(events),
        "units": _units_from_events(events),
        "sessions": _sessions_from_events(events),
        "last_event": events[-1] if events else None,
        "cost_rows": len(rows),
        "cost_total_usd": round(sum(known), 6) if known else None,
        "cost_unknown_rows": len(rows) - len(known),
    }


def _now_iso() -> str:
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


def _units_from_events(events: list[dict]) -> list[dict]:
    """One entry per unit, from its latest attach: the harness session that ran it."""
    units: dict[str, dict] = {}
    for row in events:
        if row.get("event") != "attached" or not row.get("unit"):
            continue
        unit = str(row["unit"])
        units[unit] = {
            "unit": unit,
            "role": str(row.get("role") or ""),
            "harness": str(row.get("harness") or ""),
            "session": str(row.get("session") or ""),
            "at": str(row.get("at") or ""),
        }
    return list(units.values())


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
        f"- creato: {_now_iso()}\n"
    )


def _brief_template() -> str:
    """The brief fields the supervisor must fill; every one is required."""
    return """# Brief dell'unità <unità> — ruolo <ruolo>

Chi inizia da zero, in una sessione nuova, non deve leggere altro.
Compila ogni campo, cancella queste istruzioni.

## Identificativo e ruolo
<unità> · <ruolo>

## Obiettivo
Una riga sola.

## Contesto minimo
File e percorsi esatti, comandi, vincoli.

- ...

## Risultato atteso
Il file o il commit che deve esistere alla fine.

## Confine
- File consentiti: ...
- File vietati: ...

## Verifica
```
<comando esatto>
```

## Fatto quando
La condizione osservabile che chiude l'unità.

## Cosa non fare
Niente push, niente tag, niente test esistenti modificati o cancellati, niente bump di VERSION, CHANGELOG.md o .github.

## Formato del resoconto atteso
I campi di `30-resoconto-MODELLO.md`, solo fatti con la prova incollata.
"""


def _report_template() -> str:
    """The report fields the executor must fill; every one is required."""
    return """# Resoconto dell'unità <unità> — ruolo <ruolo>

Solo fatti.
Una dichiarazione senza la prova incollata non vale.

## Stato
fatto | bloccato

## Commit sul ramo locale
- <hash> <titolo>

## File toccati
| file | righe aggiunte | righe rimosse |
|---|---|---|

## Comandi eseguiti
```
<comando>
<ultime righe del risultato, incollate>
```

## Test nuovi
Quali sono e, quando l'unità è un fix, la prova che falliscono sul codice precedente.

## Dubbi e richieste al proprietario
Cosa chiedi, senza averlo eseguito in proprio.

## Prossimo passo se bloccato
Cosa serve per ripartire.
"""
