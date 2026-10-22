"""What the supervisor left behind: stale tabs, unmarked workers, one shared server.

One job: read the machine's process table and the lane's tasks, decide which
marked tabs belong to a task that is finished or that no longer exists at all,
keep the ones whose task can still work, list the harness processes that carry
no tab, and count what hangs under the shared `opencode serve`.

Nothing here guesses: a task is finished when every unit is approved or
published, gone when the lane cannot open it any more (a temporary home deleted
under it), and open otherwise, and a scout tab is also open while its round has
not closed. Closing a stale tab follows `close_tab`'s rule, deepest first, and
never takes down a tree that holds the shared server.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime

from . import supervision_adapters as adapters
from . import supervision_processes as processes
from . import supervision_retire as retire
from . import supervision_state as state
from . import supervision_windows as windows
from .supervision_state import SupervisionError

#: The reasons a marked tab can be closed, as the report words them.
FINISHED = "finito"
GONE = "sconosciuto"
EMPTY = "senza unità"
OPEN = "aperto"
#: The scout's two events: a round that opened and did not close is still running.
SCOUT_EVENTS = ("scout_opened", "scout_finished")
#: The states a unit reaches when it has no more work in its copy.
DONE_STATES = ("pubblicata", "pubblicata in locale", "ritirata")


@dataclass(frozen=True)
class Tab:
    """One tab the engine opened, with its tree and why it may or may not be closed."""

    marker: str
    task: str
    role: str
    pids: tuple[int, ...]
    reason: str
    shared: int = 0

    @property
    def closable(self) -> bool:
        """Whether this tab is stale and safe to close."""
        return self.reason in (FINISHED, GONE, EMPTY) and not self.shared


def tabs(*, table: list[dict] | None = None) -> list[Tab]:
    """Every marked tab on this machine, with the subtree it owns and why it is where it is."""
    rows = table if table is not None else processes.rows()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        marker = windows.marker_of(row)
        if marker:
            grouped.setdefault(marker, []).append(row)
    found: list[Tab] = []
    for marker, roots in grouped.items():
        task, role = _parse(marker)
        if not task:
            continue
        tree = processes.subtree(rows, [row["pid"] for row in roots])
        shared = next((row["pid"] for _, row in tree if processes.is_shared_server(row)), 0)
        found.append(Tab(
            marker=marker, task=task, role=role,
            pids=tuple(row["pid"] for _, row in tree),
            reason=_reason(task, role), shared=shared,
        ))
    return found


def survey(*, table: list[dict] | None = None, deep: bool = False) -> dict:
    """Everything the verb prints: stale tabs, kept tabs, unmarked workers, the shared server.

    The survey also answers the owner's rule that nothing is left behind: the
    copies and branches of units the journal already closed, and, when `deep` is
    asked, the folders a shared server still holds after their copy went away.
    `deep` is off by default because answering it starts the harness's own CLI:
    a caller that brings its own process table is not made to pay for that.
    """
    rows = table if table is not None else processes.rows()
    found = tabs(table=rows)
    taken = {pid for tab in found for pid in tab.pids}
    return {
        "close": [tab for tab in found if tab.closable],
        "keep": [tab for tab in found if not tab.closable],
        "unmarked": _unmarked(rows, taken),
        "server": _server(rows),
        "leftovers": leftovers(),
        "locations": orphan_locations(table=rows) if deep else [],
    }


def close_tabs(found: list[Tab], *, killer=None) -> dict:
    """Close these stale tabs, deepest first; one that holds the shared server stays."""
    closed: list[Tab] = []
    kept: list[Tab] = []
    for tab in found:
        if tab.shared:
            kept.append(tab)
            continue
        pids = [pid for pid in tab.pids if pid != os.getpid()]
        (killer or processes.kill)(pids)
        closed.append(replace(tab, pids=tuple(pids)))
        _journal(tab)
    return {"closed": closed, "kept": kept}


def unknown_count(*, table: list[dict] | None = None) -> int:
    """How many marked tabs belong to a task that is no longer in the lane's folder."""
    return len([tab for tab in tabs(table=table) if tab.reason == GONE])


def notes(*, table: list[dict] | None = None) -> list[str]:
    """One informational line for `sup collect`: marked tabs of tasks the lane no longer has.

    It is not a gate: a proof tab left open is noise and memory, not a defect of
    the unit under review. Saying it once per collection is what makes it get
    cleaned, and `sup pulizia` is the verb that does it.
    """
    try:
        count = unknown_count(table=table)
    except SupervisionError:  # nexgen: allow reason="una nota informativa che non si può calcolare non deve far fallire la raccolta"
        return []
    if not count:
        return []
    return [
        f"schede marcate di compiti in una home temporanea ancora aperte: {count} "
        "(`sup pulizia` le elenca e `sup pulizia --chiudi` le chiude)"
    ]


def lines(found: dict) -> list[str]:
    """The survey as lines a person reads, one fact each."""
    out = [f"schede da chiudere: {len(found['close'])}"]
    out += [f"  {_tab_line(tab)}" for tab in found["close"]]
    out.append(f"schede tenute aperte: {len(found['keep'])}")
    out += [f"  {_tab_line(tab)}" for tab in found["keep"]]
    out.append(f"processi senza marcatore: {len(found['unmarked'])}")
    out += [f"  {_process_line(row)}" for row in found["unmarked"]]
    for item in found["server"]:
        out.append(
            f"server condiviso: pid {item['pid']} · {item['children']} processi figli"
        )
    if found["server"]:
        out.append(_restart_advice(found))
    out += leftover_lines(found)
    return out


def _restart_advice(found: dict) -> str:
    """Whether the shared server can be restarted now: only when nothing is running on it.

    A session is active when a marked tab of an open task exists, or when an
    `opencode` worker with no tab is still running: restarting the server would
    stop either of them, and the engine does not invite an owner to do that.
    """
    open_tabs = [tab for tab in found["keep"] if tab.reason == OPEN]
    workers = [
        row for row in found["unmarked"]
        if str(row.get("name") or "").lower().startswith("opencode")
    ]
    if open_tabs or workers:
        return "sessioni ancora attive: non riavviare il servizio ora"
    return "nessuna sessione attiva: si può riavviare il servizio"


def closed_lines(result: dict) -> list[str]:
    """What the closing did, as lines."""
    total = sum(len(tab.pids) for tab in result["closed"])
    out = [f"chiuse: {len(result['closed'])} schede ({total} processi)"]
    out += [f"  tenuta aperta: {_tab_line(tab)} (servizio condiviso)" for tab in result["kept"]]
    return out


def leftovers() -> list[dict]:
    """Copies and branches the journal already closed and the disk still has.

    Only units whose journal says their work arrived are asked: a copy that is
    still being worked in is not garbage, and listing it would invite the owner to
    delete it. The copy and the branch are reported apart because either can stay
    behind on its own.
    """
    found: list[dict] = []
    for task in state.list_tasks():
        if task.get("error"):
            continue
        try:
            units = state.status(task["id"])["units"]
        except SupervisionError:  # nexgen: allow reason="un compito che la corsia non apre più non ha copie da elencare"
            continue
        for record in units:
            if not _closed_by_journal(record):
                continue
            row = retire.leftover(task["id"], record)
            if row["copy"] or row["branch"]:
                found.append(row)
    return found


def _closed_by_journal(record: dict) -> bool:
    """Whether this unit's journal says the work arrived somewhere permanent."""
    return bool(record.get("retired") or record.get("published_head")
                or record.get("state") in DONE_STATES)


def close_leftovers(items: list[dict]) -> dict:
    """Finish removing the copies and branches of closed units; a refusal is named, not hidden."""
    closed: list[dict] = []
    problems: list[str] = []
    for row in items:
        commits = retire.verified_commits(row["task"], row["unit"])
        try:
            record = state.unit_record(row["task"], row["unit"])
            kept = [str(record["kept_head"])] if record.get("kept_head") else []
            result = retire.retire(
                row["task"], row["unit"], why="pulizia",
                commits=commits or kept or None, archive=not (commits or kept),
            )
        except SupervisionError as exc:
            problems.append(f"{row['task']} · {row['unit']}: {exc}")
            continue
        closed.append(result)
    return {"closed": closed, "problems": problems}


def leftover_lines(found: dict) -> list[str]:
    """The copy, branch and server sections of `sup pulizia`, one fact per line."""
    out = [f"copie e rami di unità finite ancora sul disco: {len(found.get('leftovers') or [])}"]
    out += [f"  {_leftover_line(row)}" for row in found.get("leftovers") or []]
    out.append(f"server MCP di una copia chiusa: {len(found.get('locations') or [])}")
    out += [f"  {_location_line(row)}" for row in found.get("locations") or []]
    if found.get("locations"):
        out.append("sono cartelle che non esistono più: `sup pulizia --chiudi` le smaltisce")
    return out


def retired_lines(result: dict) -> list[str]:
    """What closing leftovers did, named one by one."""
    out = [f"ritirate: {len(result['closed'])} unità"]
    out += [
        f"  {item['unit']}: copia {item['copy']} · ramo {item['branch']} · archivio {item['archive'] or 'nessuno'}"
        for item in result["closed"]
    ]
    out += [f"  non ritirata, {problem}" for problem in result["problems"]]
    return out


def evict_locations(items: list[dict]) -> list[str]:
    """Ask the shared server to drop the folders that no longer exist; active folders are never in `items`."""
    if not items:
        return []
    try:
        adapter = adapters.get("opencode")
    except adapters.base.AdapterError as exc:
        return [f"server del harness non identificato: {exc}"]
    lines: list[str] = []
    for row in items:
        try:
            ok, reason = adapter.dispose_location(row["directory"])
        except adapters.base.AdapterError as exc:
            lines.append(f"non smaltita {row['directory']}: {exc}")
            continue
        lines.append(("smaltita " if ok else "non smaltita ") + f"{row['directory']}: {reason}")
    return lines


def orphan_locations(*, lister=None, exists=None, table=None) -> list[dict]:
    """Folders a shared server still holds although the folder is gone, with when its first process started.

    The server's debug API lists the directories it holds, not when it took them;
    the start time therefore comes from the process table, taking the oldest
    process whose command line names that folder. A machine where neither source
    answers lists nothing instead of inventing a row.
    """
    try:
        folders = (lister or adapters.get("opencode").locations)()
    except (SupervisionError, adapters.base.AdapterError):  # nexgen: allow reason="un servizio che non risponde non elenca cartelle: la riga manca invece di essere inventata"
        return []
    is_there = exists or (lambda value: os.path.isdir(value))
    rows = table if table is not None else _safe_rows()
    found: list[dict] = []
    for folder in folders:
        if not folder or is_there(folder):
            continue
        found.append({"directory": folder, "started": _first_start(rows, folder)})
    return found


def _safe_rows() -> list[dict]:
    """The process table, or nothing when it cannot be read: the listing is a report, not a gate."""
    try:
        return processes.rows()
    except SupervisionError:  # nexgen: allow reason="una tabella di processi illeggibile non impedisce di elencare le cartelle"
        return []


def _first_start(rows: list[dict], folder: str) -> str:
    """When the oldest process naming this folder started, or an empty string when nobody says."""
    moments = [
        float(row["created"]) for row in rows
        if folder in str(row.get("command_line") or "") and isinstance(row.get("created"), (int, float))
    ]
    if not moments:
        return ""
    return datetime.fromtimestamp(min(moments)).isoformat(timespec="seconds")


def _leftover_line(row: dict) -> str:
    """One closed unit still on disk: what is left, and the state the journal recorded."""
    parts = [row["task"], row["unit"], row["state"] or "stato non letto"]
    if row["copy"]:
        parts.append(f"copia {row['copy']}")
    if row["branch"]:
        parts.append(f"ramo {row['branch']}")
    return " · ".join(parts)


def _location_line(row: dict) -> str:
    """One folder the shared server holds after its copy went: where, and since when."""
    when = row.get("started") or "ora di avvio non esposta dal server"
    return f"{row['directory']} · {when}"


def _tab_line(tab: Tab) -> str:
    """One tab: which task, which role, why, how many processes, and the server if it holds it."""
    shared = f" · servizio condiviso {tab.shared}" if tab.shared else ""
    return f"{tab.task} · {tab.role} · {tab.reason} · {len(tab.pids)} processi{shared}"


def _process_line(row: dict) -> str:
    """One unmarked process: name, pid, and the beginning of what it runs."""
    command = str(row.get("command_line") or "")[:100]
    return f"{row.get('name', '')} {row.get('pid', '')} · {command}".strip()


def _parse(marker: str) -> tuple[str, str]:
    """The task and the role a marker names; an unknown shape is not a tab of this engine."""
    parts = marker.split(":")
    if len(parts) < 3:
        return "", ""
    return parts[1], ":".join(parts[2:])


def _reason(task_id: str, role: str) -> str:
    """Whether a task can still work in this tab: finished, gone, without units, or open.

    The scout check comes before the unit count: a reconnaissance started right
    after `sup start` works before the first unit exists, and calling that task
    "senza unità" would let `sup pulizia --chiudi` kill a live round.
    """
    try:
        data = state.status(task_id)
    except SupervisionError:  # nexgen: allow reason="un compito che la corsia non apre più è proprio quello che la pulizia deve chiudere"
        return GONE
    if role == "scout" and _scout_running(task_id):
        return OPEN
    units = data["units"]
    if not units:
        return EMPTY
    open_units = [
        record["unit"] for record in units
        if not (record.get("approved") or record.get("published"))
    ]
    if open_units:
        return OPEN
    return FINISHED


def _scout_running(task_id: str) -> bool:
    """Whether the task's last scout round opened and never closed."""
    last = ""
    for row in state.read_events(task_id):
        if row.get("event") in SCOUT_EVENTS:
            last = str(row["event"])
    return last == SCOUT_EVENTS[0]


def _unmarked(rows: list[dict], taken: set[int]) -> list[dict]:
    """Harness processes that no tab of this engine holds."""
    found: list[dict] = []
    for row in rows:
        if row["pid"] in taken or processes.is_shared_server(row):
            continue
        name = str(row.get("name") or "").lower()
        if name.startswith("opencode") or name.startswith("agy"):
            found.append(row)
    return found


def _server(rows: list[dict]) -> list[dict]:
    """The shared harness servers, each with how many processes hang under it."""
    found: list[dict] = []
    for row in rows:
        if not processes.is_shared_server(row):
            continue
        children = len(processes.subtree(rows, [row["pid"]])) - 1
        found.append({"pid": row["pid"], "children": max(children, 0)})
    return found


def _journal(tab: Tab) -> None:
    """Record the closing on the task's journal, when the task still has one."""
    if tab.reason == GONE:
        return
    state.append_event(tab.task, "tab_closed", role=tab.role, pids=list(tab.pids), why="pulizia")
