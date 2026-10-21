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


def close_tab(task_id: str, role: str, *, lister=None, killer=None) -> dict:
    """Close the tab that ran this role, unless closing it would kill work still running.

    The tab is a shell whose command line carries the exact assignment this
    engine wrote; under it are the harness and whatever the harness started.
    Killing the shell closes the tab and killing the harness ends its work, and
    nothing is killed when the tab is still needed. For the executor that means
    another unit of the same task may still be created, queued, running or
    waiting for a decision, and the task has one session for all of them: then
    the tab stays, `tab_left_open` names the units, and the owner sees why. Nor
    is a tree holding an `opencode serve` ever closed: the first tab of the day
    starts the server every other session shares, so closing that one would stop
    them all. A tab that is no longer there is not an error: the engine closes
    what it opened, and nothing else.
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
        still_open = _open_units(task_id)
        if still_open:
            reason = (
                "altre unità del compito possono ancora lavorare nella stessa sessione "
                f"({', '.join(still_open)}): chiudo la scheda quando sono pubblicate"
            )
            state.append_event(
                task_id, "tab_left_open", role=role, reason=reason, units=still_open,
            )
            return _tab_result(role, [], reason, found=True)
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


def _tab_result(role: str, pids: list[int], reason: str, *, found: bool = False) -> dict:
    """What happened, as one dict and one line the verb can print."""
    if pids:
        line = f"scheda {role} chiusa ({len(pids)} processi)"
    elif reason:
        line = f"scheda {role} lasciata aperta: {reason}"
    else:
        line = f"nessuna scheda {role} da chiudere"
    return {"role": role, "found": found, "closed": list(pids), "reason": reason, "line": line}


def _is_tab_shell(row: dict, marker: str) -> bool:
    """Whether this row is the shell the engine opened for this tab, and not a command citing it."""
    return marker_of(row) == marker


def _open_units(task_id: str) -> list[str]:
    """The task's units whose executor may still put work into the one session.

    A task has one executor session for all its units, so a published unit does
    not free the tab while a second one is created, queued, running, stalled or
    waiting for a decision: closing it would kill that unit's work. A unit that
    is approved and published, or approved and waiting only for `sup publish`,
    has no work left.
    """
    found: list[str] = []
    for record in state.status(task_id)["units"]:
        if record.get("approved") or record.get("published"):
            continue
        found.append(str(record.get("unit") or ""))
    return found


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

    The shell exports the marker before it runs the command, so the tab can be
    found later in `/proc/<pid>/environ` and closed without guessing.
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
    return terminal, ([terminal, title_flag, title, exec_flag, *inner], kwargs)


def _linux_terminal() -> tuple[str, str, str]:
    """The first terminal on this machine and the flags it wants."""
    for name, title_flag, exec_flag in _LINUX_TERMINALS:
        found = shutil.which(name)
        if found:
            return found, title_flag, exec_flag
    wanted = ", ".join(name for name, _, _ in _LINUX_TERMINALS)
    raise WindowError(f"nessun terminale grafico trovato: cerca {wanted}")
