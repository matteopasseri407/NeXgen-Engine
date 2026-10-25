"""The shared harness server and its old children: one rule for what may be collected.

One job: decide which processes that hang under the shared `opencode serve` belong to
sessions that are already over, and stop exactly those. The server starts one MCP set
and one shell per folder it serves, and when a session ends nobody collects them: on
the owner's PC one server carried 1326 descendants, 944 of them born before the
oldest live session, and the machine had 14 GB free of 64.

The rule is the one he applied by hand, and it is deliberately narrow:

- a *direct* child of the server, born before the oldest live session started, with
  its whole tree (an MCP server and the `conhost` under it die together);
- with no live session at all, every direct child of the server;
- never the server itself, never a child born after that moment, never a row whose
  birth time nobody reports, and nothing that does not descend from the server.

A wrong pid here costs a session in the middle of its work, so the broad case needs
an empty machine and the narrow case needs a proven birth time. What is left to the
owner is only the question of restarting the service itself, which
`supervision_cleanup` still answers.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from . import supervision_processes as processes
from . import supervision_windows as windows
from .supervision_state import SupervisionError


def runs_harness(row: dict) -> bool:
    """Whether this row is a harness process someone may be watching, the server excluded."""
    name = str(row.get("name") or "").lower()
    if not (name.startswith("opencode") or name.startswith("agy")):
        return False
    return not processes.is_shared_server(row)


def is_session(row: dict) -> bool:
    """Whether this row is a live piece of work that holds a folder open.

    A marked tab is one, and so is any harness process that is not the server: a
    session the engine never opened a tab for still has a folder whose MCP servers
    must survive, and only its start time can protect them.
    """
    return bool(windows.marker_of(row)) or runs_harness(row)


def oldest_session(table: list[dict]) -> float | None:
    """When the oldest live session started, or None when nothing is alive on this machine."""
    shared = set(servers(table))
    moments = [
        float(row["created"]) for row in table
        if isinstance(row.get("created"), (int, float))
        and (is_session(row) or _node_harness(row, shared))
    ]
    return min(moments) if moments else None


def _node_harness(row: dict, shared: set[int]) -> bool:
    """An OpenCode installed as a script runs inside node, and it is a session too.

    Same fact as a compiled binary, different process name. A `node` child of the
    server is never a session: those are exactly the MCP servers this pass collects,
    and calling them sessions would protect every one of them for ever.
    """
    if Path(str(row.get("name") or "")).stem.lower() != "node":
        return False
    if row.get("ppid") in shared:
        return False
    return "opencode" in str(row.get("command_line") or "").lower().replace("\\", "/")


def servers(table: list[dict]) -> list[int]:
    """The pids of every shared harness server on this machine."""
    return [row["pid"] for row in table if processes.is_shared_server(row)]


def children(table: list[dict]) -> list[dict]:
    """Every direct child of a shared server."""
    shared = set(servers(table))
    return [
        row for row in table
        if row.get("ppid") in shared and row["pid"] not in shared
    ]


def plan(table: list[dict]) -> tuple[float | None, list[dict]]:
    """The moment that separates old from new, and the processes that moment allows stopping.

    A child whose birth time is unknown is not old: a session that started a moment
    ago would lose its MCP servers over a row Windows refused to describe.
    """
    before = oldest_session(table)
    roots: list[int] = []
    for row in children(table):
        if before is None:
            roots.append(row["pid"])
        elif isinstance(row.get("created"), (int, float)) and row["created"] < before:
            roots.append(row["pid"])
    tree = processes.subtree(table, roots)
    guards = set(servers(table)) | {os.getpid()}
    return before, [row for _, row in tree if row["pid"] not in guards]


def survey(table: list[dict], *, planned: tuple[float | None, list[dict]] | None = None) -> dict:
    """What the collection would do, without doing it: how many children, and since when."""
    before, targets = planned if planned is not None else plan(table)
    return {
        "servers": len(servers(table)),
        "children": len(children(table)),
        "stale": len(targets),
        "since": _when(before),
        "alive": before is not None,
    }


def collect(*, table: list[dict] | None = None, killer=None) -> dict:
    """Stop the stale children of every shared server and report what went.

    A table that cannot be read is a note, not a failure: this pass runs at the end of
    a publication, and a publication that already landed cannot be undone by it.
    """
    try:
        rows = table if table is not None else processes.rows()
    except SupervisionError as exc:  # nexgen: allow reason="una tabella illeggibile non deve far fallire la pubblicazione già avvenuta"
        return {
            "error": str(exc), "servers": 0, "children": 0, "stale": 0,
            "since": "", "alive": False, "closed": [],
        }
    found = plan(rows)
    closed = [row["pid"] for row in found[1]]
    (killer or processes.kill)(closed)
    return {**survey(rows, planned=found), "closed": closed}


def lines(found: dict) -> list[str]:
    """The collection as lines a person reads: how many children, and which moment decides."""
    if found.get("error"):
        return [f"figli del server condiviso: non leggibili ({found['error']})"]
    if not found.get("servers"):
        return ["figli del server condiviso: nessun server in esecuzione, niente da raccogliere"]
    out = [
        f"figli del server condiviso: {found.get('children', 0)} processi diretti, "
        f"{found.get('stale', 0)} di sessioni chiuse"
    ]
    if found.get("alive"):
        began = f", iniziata {found['since']}" if found.get("since") else ""
        out.append(f"  si raccolgono quelli nati prima della sessione attiva più vecchia{began}")
    else:
        out.append("  nessuna sessione attiva: si raccolgono tutti i figli diretti del server")
    if found.get("closed"):
        out.append(f"  raccolti: {len(found['closed'])} processi")
    return out


def _when(moment: float | None) -> str:
    """The moment as a date the owner can read, or nothing when the table does not say.

    Windows reports an epoch second; Linux `/proc` reports clock ticks since boot,
    which are only comparable inside one table, so turning them into a date would
    print a 1970 morning that never happened. The moment still decides what is
    collected; it is only the printed date that is dropped.
    """
    if not isinstance(moment, (int, float)) or moment < 1_600_000_000:
        return ""
    return datetime.fromtimestamp(moment).isoformat(timespec="seconds")
