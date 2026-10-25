"""Visible terminal windows for a supervised unit: a tab of the one supervisor window.

A supervised job is only trustworthy if the owner can watch it: every harness
runs in a real terminal tab that stays open at the end, and the engine never
reads that tab's output. Windows Terminal gets one named window with a tab per
session, so ten units are ten tabs and not ten windows. When a window cannot be
opened this module raises with the reason instead of falling back to a hidden
process, which is exactly the silent execution it exists to prevent.

A tab is also closed from here, once its work is published, and never blindly:
the shell carries a marker this engine wrote, the harness is found under it, and
a tree that holds the shared `opencode serve` is left alone. Killing that server
would close every other worker the owner has running. On Linux the tab is a
terminal window whose shell exports the marker, so the same rule reads the
process's environment instead of its command line.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from nexgen_core.processes import powershell_literal

from . import supervision_processes as processes
from . import supervision_state as state
from .supervision_state import SupervisionError


class WindowError(SupervisionError):
    """No graphical session, no terminal installed, no shell to keep the window open."""


#: Every supervised session lives as a tab of this one Windows Terminal window.
WINDOW_NAME = "nexgen-sup"
#: The variable whose value names the tab in the process table, once the shell starts.
TAB_ENV = "NEXGEN_SUP_TAB"
#: The prefix every marker starts with; it is what makes a value a tab and not a coincidence.
TAB_PREFIX = "nexgen-sup:"
#: The assignment the engine writes into a Windows shell's command line.
_ASSIGNMENT = re.compile(rf"\$env:{TAB_ENV} = '([^']*)'")
#: How a tab's command line names the unit it serves: the copy folder it was opened on,
#: and the window title the engine gave it.
_UNIT_IN_COPY = re.compile(r"23-unita-([A-Za-z0-9._-]+)")
_UNIT_IN_TITLE = re.compile(r"esecutore\s*\u00b7\s*([A-Za-z0-9._-]+)")
#: The table and the kill are the processes module's job; these names stay the injection points here.
_processes = processes.rows
_kill = processes.kill



#: Linux terminals, first found wins; each wants the title and the command differently.
_LINUX_TERMINALS: tuple[tuple[str, str, str], ...] = (
    ("x-terminal-emulator", "-T", "-e"),
    ("gnome-terminal", "--title", "--"),
    ("konsole", "--title", "-e"),
    ("xterm", "-T", "-e"),
)


def open_window(title: str, argv: Sequence[str], *, cwd: str | Path, marker: str = "") -> dict:
    """Open a visible terminal that runs `argv` and stays open; return what started.

    Raises ``WindowError`` when the platform cannot show one. Nothing is ever
    launched hidden: every refusing branch raises before a process starts.
    `marker` is the tab's name in the process table, so `close_tab` can find this
    tab later without guessing and without touching another one.
    """
    command = [str(token) for token in argv]
    if not command:
        raise WindowError("comando vuoto: una finestra senza comando non serve a niente")
    folder = Path(cwd)
    if not folder.is_dir():
        raise WindowError(f"cartella di lavoro inesistente: {folder}")
    if os.name == "nt":
        launcher, spawned = _windows_window(title, command, folder, marker)
    else:
        launcher, spawned = _linux_window(title, command, folder, marker)
    try:
        proc = subprocess.Popen(spawned[0], **spawned[1])
    except OSError as exc:
        raise WindowError(
            f"finestra non aperta: {launcher} ha rifiutato l'avvio ({type(exc).__name__}: {exc})"
        ) from exc
    return {
        "title": title,
        "launcher": launcher,
        "argv": spawned[0],
        "cwd": str(folder),
        "pid": getattr(proc, "pid", None),
        "marker": marker,
    }


def tab_marker(task_id: str, role: str) -> str:
    """The one string that names a supervisor tab in the machine's process table."""
    return f"{TAB_PREFIX}{task_id}:{role}"


def marker_parts(marker: str) -> tuple[str, str]:
    """The task and the role a tab marker names; an unknown shape is not a tab of this engine."""
    parts = marker.split(":")
    if len(parts) < 3:
        return "", ""
    return parts[1], ":".join(parts[2:])


def marker_of(row: dict) -> str:
    """The tab marker this process carries, in the exact form this engine wrote, or "".

    On Windows the engine writes one assignment into the shell's command line; on
    Linux the shell exports the variable, so it is in the process's environment.
    A `grep`, an editor or a `cmd` that merely quotes the marker carries neither,
    and is never mistaken for a tab.
    """
    if not processes.is_shell(row):
        return ""
    if os.name == "nt":
        found = _ASSIGNMENT.search(str(row.get("command_line") or ""))
        value = found.group(1) if found else ""
    else:
        value = ""
        for entry in str(row.get("environ") or "").split("\0"):
            if entry.startswith(f"{TAB_ENV}="):
                value = entry.split("=", 1)[1]
                break
    return value if value.startswith(TAB_PREFIX) else ""


def unit_tab_open(task_id: str, unit: str) -> bool:
    """Whether the journal says a tab of this unit was opened and never closed.

    One unit, one tab: this is the question asked before a new tab replaces the old
    one and at retirement, and it is what keeps a task with no window of its own from
    paying for a process-table read. A `tab_closed` row that does not name the unit
    (the task-wide close in `sup pulizia`) also clears it: the tab it closed may have
    been this unit's, and looking again is cheaper than opening a second one.
    """
    name = state.slug(unit)
    opened = False
    for row in state.read_events(task_id):
        event = str(row.get("event") or "")
        if event in ("unit_started", "unit_reworked") and row.get("mode") == "tab":
            if str(row.get("unit") or "") == name:
                opened = True
        elif event == "tab_closed":
            owner = str(row.get("unit") or "")
            if not owner or owner == name:
                opened = False
    return opened


def unit_of(row: dict) -> str:
    """The unit this tab's command line names, from its copy folder or its window title.

    The marker names the task and the role, and a task has one executor marker for
    every unit: what makes a tab belong to a unit is the folder the tab was opened on
    and the title the engine gave it. A command line that names neither is not a tab
    of a unit, and closing one is never based on a guess.
    """
    command_line = str(row.get("command_line") or "")
    for pattern in (_UNIT_IN_COPY, _UNIT_IN_TITLE):
        found = pattern.search(command_line)
        if found:
            return found.group(1)
    return ""


def close_unit_tab(
    task_id: str, unit: str, *, role: str = "esecutore", lister=None, killer=None,
) -> dict:
    """Close the tab this engine opened for one unit, and only that tab.

    One unit, one tab: a new tab for the same unit replaces the old one, and a unit
    that is retired takes its tab with it. The candidates are the shells that carry
    the engine's own marker for this task and role and whose command line names this
    unit; a tree that holds the shared `opencode serve` is left open, exactly as in
    `close_tab`, and a process without the marker is never a candidate.
    """
    marker = tab_marker(task_id, role)
    wanted = state.slug(unit)
    try:
        table = (lister or _processes)()
    except (processes.ProcessError, WindowError, OSError, ValueError) as exc:
        return _tab_result(role, [], f"processi non leggibili ({exc})", unit=wanted)
    roots = [row for row in table if _is_tab_shell(row, marker) and unit_of(row) == wanted]
    if not roots:
        return _tab_result(role, [], "", unit=wanted)
    tree = processes.subtree(table, [row["pid"] for row in roots])
    shared = [row for _, row in tree if processes.is_shared_server(row)]
    if shared:
        reason = (
            f"la scheda {role} di {wanted} tiene in piedi il servizio condiviso del harness "
            f"(pid {shared[0]['pid']}): non la chiudo, chiudila a mano"
        )
        state.append_event(task_id, "tab_left_open", role=role, unit=wanted, reason=reason)
        return _tab_result(role, [], reason, found=True, unit=wanted)
    pids = [row["pid"] for _, row in tree if row["pid"] != os.getpid()]
    (killer or _kill)(pids)
    state.append_event(task_id, "tab_closed", role=role, unit=wanted, pids=pids, why="sostituita")
    return _tab_result(role, pids, "", found=True, unit=wanted)


def close_tab(
    task_id: str, role: str, *, lister=None, killer=None,
    still_working: tuple[str, ...] = (), stopped: bool = True,
    unit: str = "",
) -> dict:
    """Close the tab that ran this role, unless closing it would kill work still running.

    The tab is a shell whose command line carries the exact assignment this
    engine wrote; under it are the harness and whatever the harness started.
    Killing the shell closes the tab and killing the harness ends its work, and
    nothing is killed when the tab is still needed. For the executor that means
    the session may still be writing (`stopped` false), or another unit sharing
    this tab may still be working: a tab shared between multiple units closes
    only when the last unit using it is done. Independent copies close their tab
    without waiting for units in other copies. Nor is a tree holding an
    `opencode serve` ever closed: the first tab of the day starts the server
    every other session shares, so closing that one would stop them all. A tab
    that is no longer there is not an error: the engine closes what it opened,
    and nothing else.
    """
    marker = tab_marker(task_id, role)
    try:
        table = (lister or _processes)()
    except (processes.ProcessError, WindowError, OSError, ValueError) as exc:
        return _tab_result(role, [], f"processi non leggibili ({exc})")
    roots = [row for row in table if _is_tab_shell(row, marker)]
    if not roots:
        return _tab_result(role, [], "")
    if role == "esecutore":
        blocked, roots = _check_executor_active(task_id, roots, table, unit, still_working, stopped)
        if blocked:
            return blocked
    tree = processes.subtree(table, [row["pid"] for row in roots])
    shared = [row for _, row in tree if processes.is_shared_server(row)]
    if shared:
        reason = (
            f"la scheda {role} tiene in piedi il servizio condiviso del harness "
            f"(pid {shared[0]['pid']}): non la chiudo, chiudila a mano"
        )
        state.append_event(
            task_id, "tab_left_open", role=role, reason=reason,
            pids=[row["pid"] for _, row in tree],
        )
        return _tab_result(role, [], reason, found=True)
    pids = [row["pid"] for _, row in tree if row["pid"] != os.getpid()]
    (killer or _kill)(pids)
    state.append_event(task_id, "tab_closed", role=role, pids=pids)
    return _tab_result(role, pids, "", found=True)


def _tab_result(role: str, pids: list[int], reason: str, *, found: bool = False, unit: str = "") -> dict:
    """What happened, as one dict and one line the verb can print."""
    who = f"{role} di {unit}" if unit else role
    if pids:
        line = f"scheda {who} chiusa ({len(pids)} processi)"
    elif reason:
        line = f"scheda {who} lasciata aperta: {reason}"
    else:
        line = f"nessuna scheda {who} da chiudere"
    return {"role": role, "found": found, "closed": list(pids), "reason": reason, "line": line}


def _check_executor_active(
    task_id: str, roots: list[dict], table: list[dict], unit: str,
    still_working: tuple[str, ...], stopped: bool,
) -> tuple[dict | None, list[dict]]:
    """Verify whether an executor tab can be closed, returning (result, effective_roots)."""
    if not stopped:
        reason = "la sessione dell'esecutore sta ancora lavorando: la scheda resta aperta"
        state.append_event(task_id, "tab_left_open", role="esecutore", reason=reason)
        return _tab_result("esecutore", [], reason, found=True), roots

    unit_roots = _filter_unit_roots(roots, table, task_id, unit) if unit else []
    is_isolated = bool(unit and unit_roots and len(unit_roots) < len(roots))
    if is_isolated:
        still_open = _sharing_open_units(task_id, unit, except_units=still_working)
        effective = unit_roots
    else:
        still_open = _open_units(task_id, except_units=still_working)
        effective = roots

    if still_open:
        reason = (
            "altre unità del compito possono ancora lavorare nella stessa sessione "
            f"({', '.join(still_open)}): chiudo la scheda quando sono pubblicate"
        )
        state.append_event(task_id, "tab_left_open", role="esecutore", reason=reason, units=still_open)
        return _tab_result("esecutore", [], reason, found=True), effective
    return None, effective



def _is_tab_shell(row: dict, marker: str) -> bool:
    """Whether this row is the shell the engine opened for this tab, and not a command citing it."""
    return marker_of(row) == marker


def _is_unit_done(record: dict) -> bool:
    """Whether a unit has reached a terminal state with no more executor work."""
    if record.get("approved") or record.get("published") or record.get("retired"):
        return True
    return record.get("state") in ("pubblicata", "pubblicata in locale", "ritirata")


def _open_units(task_id: str, *, except_units: tuple[str, ...] = ()) -> list[str]:
    """The task's units whose executor may still put work into the one session."""
    ignored = {state.slug(name) for name in except_units if name}
    found: list[str] = []
    for record in state.status(task_id)["units"]:
        if record["unit"] in ignored or _is_unit_done(record):
            continue
        found.append(str(record.get("unit") or ""))
    return found


def _sharing_open_units(task_id: str, unit: str, *, except_units: tuple[str, ...] = ()) -> list[str]:
    """Other open units that actually share the same session, copy or tab with `unit`."""
    target_slug = state.slug(unit)
    ignored = {state.slug(name) for name in except_units if name}
    ignored.add(target_slug)

    units_data = state.status(task_id)["units"]
    target_rec = next((r for r in units_data if r["unit"] == target_slug), None)
    if not target_rec:
        return _open_units(task_id, except_units=except_units)

    target_session = str(target_rec.get("session") or "")
    target_wt = os.path.normcase(os.path.normpath(str(target_rec.get("worktree") or ""))) if target_rec.get("worktree") else ""
    events = state.read_events(task_id)
    target_pids = {row.get("pid") for row in events if row.get("unit") == target_slug and row.get("pid")}

    sharing_open: list[str] = []
    for record in units_data:
        name = record["unit"]
        if name in ignored or _is_unit_done(record):
            continue
        rec_session = str(record.get("session") or "")
        rec_wt = os.path.normcase(os.path.normpath(str(record.get("worktree") or ""))) if record.get("worktree") else ""
        rec_pids = {row.get("pid") for row in events if row.get("unit") == name and row.get("pid")}

        shares = (
            (target_session and target_session == rec_session)
            or (target_wt and target_wt == rec_wt)
            or bool(target_pids and (target_pids & rec_pids))
        )
        if shares:
            sharing_open.append(str(name))
    return sharing_open


def _filter_unit_roots(roots: list[dict], table: list[dict], task_id: str, unit: str) -> list[dict]:
    """Isolate tab roots belonging specifically to this unit by pid, window title or copy folder."""
    target_slug = state.slug(unit)
    events = state.read_events(task_id)
    unit_pids = {row.get("pid") for row in events if row.get("unit") == target_slug and row.get("pid")}

    unit_roots: list[dict] = []
    for root in roots:
        tree = processes.subtree(table, [root["pid"]])
        all_tree_rows = [root] + [r for _, r in tree]
        matched = any(
            r["pid"] in unit_pids or unit_of(r) == target_slug
            for r in all_tree_rows
        )
        if matched:
            unit_roots.append(root)
    return unit_roots if unit_roots else roots


def _windows_window(
    title: str, command: list[str], folder: Path, marker: str = "",
) -> tuple[str, tuple[list[str], dict]]:
    """Windows Terminal when installed, else a dedicated console; both stay open."""
    try:
        shell = processes.powershell_executable()
    except processes.ProcessError as exc:
        raise WindowError(str(exc)) from exc
    script = _powershell_invocation(command)
    if marker:
        script = f"$env:{TAB_ENV} = {powershell_literal(marker)}\n{script}"
    terminal = shutil.which("wt.exe")
    if terminal:
        argv = [
            terminal, "-w", WINDOW_NAME, "new-tab",
            "--title", _wt_value(title),
            "-d", _wt_value(str(folder)),
            _wt_value(shell), "-NoExit", "-Command", _wt_value(script),
        ]
        return terminal, (argv, {})
    titled = f"$Host.UI.RawUI.WindowTitle = {powershell_literal(title)}\n{script}"
    argv = [shell, "-NoExit", "-Command", titled]
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
    return shell, (argv, {"cwd": str(folder), "creationflags": flags})


def _wt_value(text: str) -> str:
    """A value Windows Terminal must take literally, `;` included.

    wt splits its whole command line on `;` before any shell sees it, so a
    prompt or a folder holding one would start a second subcommand instead of
    arriving intact. `\\;` is the literal semicolon wt accepts, and it unescapes
    it before the shell runs: verified against the real wt on 2026-10-20.
    """
    return text.replace(";", r"\;")


def _powershell_executable() -> str:
    """Kept for the window tests: where PowerShell is, is the process module's answer."""
    return processes.powershell_executable()


def _powershell_invocation(command: list[str]) -> str:
    """The command as a PowerShell call, quoted with the engine's own rule."""
    quoted = " ".join(powershell_literal(token) for token in command)
    return f"& {quoted}"


def _linux_window(
    title: str, command: list[str], folder: Path, marker: str = "",
) -> tuple[str, tuple[list[str], dict]]:
    """The first graphical terminal installed; the shell stays after the command ends.

    The marker goes into the environment handed to the process, because
    `/proc/<pid>/environ` only shows what `execve()` received: an `export`
    inside `bash -c` does not reach it while the command runs. The export stays
    for the final shell the command leaves behind.
    """
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise WindowError(
            "nessuna sessione grafica: DISPLAY e WAYLAND_DISPLAY assenti, "
            "la finestra non si vedrebbe e il motore non esegue alla cieca"
        )
    terminal, title_flag, exec_flag = _linux_terminal()
    parts = []
    if marker:
        parts.append(f"export {TAB_ENV}={shlex.quote(marker)}")
    parts.append(shlex.join(command) + "; exec bash")
    inner = ["bash", "-c", "; ".join(parts)]
    # The terminal owns the window: detach it from this process group so a
    # timeout or a Ctrl-C here never closes what the owner is watching.
    kwargs = {
        "cwd": str(folder),
        "start_new_session": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if marker:
        kwargs["env"] = {**os.environ, TAB_ENV: marker}
    return terminal, ([terminal, title_flag, title, exec_flag, *inner], kwargs)


def _linux_terminal() -> tuple[str, str, str]:
    """The first terminal on this machine and the flags it wants."""
    for name, title_flag, exec_flag in _LINUX_TERMINALS:
        found = shutil.which(name)
        if found:
            return found, title_flag, exec_flag
    wanted = ", ".join(name for name, _, _ in _LINUX_TERMINALS)
    raise WindowError(f"nessun terminale grafico trovato: cerca {wanted}")
