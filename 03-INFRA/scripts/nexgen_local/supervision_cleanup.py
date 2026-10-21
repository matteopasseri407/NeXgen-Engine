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

from . import supervision_processes as processes
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


def survey(*, table: list[dict] | None = None) -> dict:
    """Everything the verb prints: stale tabs, kept tabs, unmarked workers, the shared server."""
    rows = table if table is not None else processes.rows()
    found = tabs(table=rows)
    taken = {pid for tab in found for pid in tab.pids}
    return {
        "close": [tab for tab in found if tab.closable],
        "keep": [tab for tab in found if not tab.closable],
        "unmarked": _unmarked(rows, taken),
        "server": _server(rows),
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
    """Whether a task can still work in this tab: finished, gone, without units, or open."""
    try:
        data = state.status(task_id)
    except SupervisionError:  # nexgen: allow reason="un compito che la corsia non apre più è proprio quello che la pulizia deve chiudere"
        return GONE
    units = data["units"]
    if not units:
        return EMPTY
    open_units = [
        record["unit"] for record in units
        if not (record.get("approved") or record.get("published"))
    ]
    if open_units:
        return OPEN
    if role == "scout" and _scout_running(task_id):
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
