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
would close every other worker the owner has running.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from nexgen_core.processes import powershell_literal

from . import supervision_state as state
from .supervision_state import SupervisionError


class WindowError(SupervisionError):
    """No graphical session, no terminal installed, no shell to keep the window open."""


#: Every supervised session lives as a tab of this one Windows Terminal window.
WINDOW_NAME = "nexgen-sup"
#: The variable whose value names the tab in the process table, once the shell starts.
TAB_ENV = "NEXGEN_SUP_TAB"
#: How long one PowerShell question about the process table may take.
PROCESS_TIMEOUT_S = 30.0
_LIST_PROCESSES = (
    "Get-CimInstance Win32_Process | "
    "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
)
_KILL_PROCESSES = "Stop-Process -Id {pids} -Force -ErrorAction SilentlyContinue"



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
        launcher, spawned = _linux_window(title, command, folder)
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
    return f"nexgen-sup:{task_id}:{role}"


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
    if os.name != "nt":
        return _tab_result(role, [], "la chiusura della scheda è gestita solo su Windows")
    try:
        table = (lister or _processes)()
    except (WindowError, OSError, ValueError) as exc:
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
    tree = _descendants(table, [row["pid"] for row in roots])
    shared = [row for _, row in tree if _is_shared_server(row)]
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


def _descendants(table: list[dict], roots: list[int]) -> list[tuple[int, dict]]:
    """Every process under these roots with its depth, deepest first, so children die first."""
    by_pid = {row["pid"]: row for row in table}
    by_parent: dict[int, list[dict]] = {}
    for row in table:
        by_parent.setdefault(row["ppid"], []).append(row)
    found: list[tuple[int, dict]] = []
    seen: set[int] = set()
    stack = [(pid, 0) for pid in roots]
    while stack:
        pid, depth = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        node = by_pid.get(pid)
        if node is not None:
            found.append((depth, node))
        for child in by_parent.get(pid, []):
            stack.append((child["pid"], depth + 1))
    return sorted(found, key=lambda item: item[0], reverse=True)


def _is_tab_shell(row: dict, marker: str) -> bool:
    """Whether this row is the shell the engine opened for this tab, and not a command citing it.

    The engine writes one exact assignment into the shell's command line
    (`$env:NEXGEN_SUP_TAB = '<marcatore>'`), so the marker alone is not enough: a
    `grep`, an editor, or a `cmd` on a file that quotes it would otherwise be a
    tab to kill. Only a PowerShell process carries that assignment.
    """
    name = Path(str(row.get("name") or "")).name.lower()
    if name not in ("pwsh", "pwsh.exe", "powershell", "powershell.exe"):
        return False
    assignment = f"$env:{TAB_ENV} = {powershell_literal(marker)}"
    return assignment in str(row.get("command_line") or "")


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


def _is_shared_server(row: dict) -> bool:
    """Whether this row is the `opencode serve` that every session on the machine shares."""
    name = Path(str(row.get("name") or "")).name.lower()
    if not name.startswith("opencode"):
        return False
    return re.search(r"\bserve\b", str(row.get("command_line") or "")) is not None


def _processes() -> list[dict]:
    """Every process on the machine, as the tab finder reads it."""
    output = _powershell(_LIST_PROCESSES)
    try:
        parsed = json.loads(output or "[]")
    except ValueError as exc:
        raise WindowError(f"elenco dei processi illeggibile ({exc})") from exc
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [_process(row) for row in parsed if isinstance(row, dict)]


def _process(row: dict) -> dict:
    """One CIM row in the shape the rest of this module uses."""
    return {
        "pid": int(row.get("ProcessId") or 0),
        "ppid": int(row.get("ParentProcessId") or 0),
        "name": str(row.get("Name") or ""),
        "command_line": str(row.get("CommandLine") or ""),
    }


def _powershell(script: str) -> str:
    """One PowerShell question, with the reason when it cannot answer."""
    try:
        done = subprocess.run(
            [_powershell_executable(), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=PROCESS_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WindowError(f"PowerShell non ha risposto ({type(exc).__name__})") from exc
    if done.returncode:
        detail = (done.stderr or "").strip()[:200]
        raise WindowError(f"PowerShell esce con {done.returncode}: {detail}")
    return done.stdout


def _kill(pids: list[int]) -> None:
    """Stop these processes; one that is already gone is not a failure."""
    if not pids:
        return
    _powershell(_KILL_PROCESSES.format(pids=",".join(str(pid) for pid in pids)))


def _windows_window(
    title: str, command: list[str], folder: Path, marker: str = "",
) -> tuple[str, tuple[list[str], dict]]:
    """Windows Terminal when installed, else a dedicated console; both stay open."""
    shell = _powershell_executable()
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
    """Windows PowerShell 7 when present, the one Windows always has otherwise."""
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    raise WindowError("PowerShell non trovato: impossibile aprire una finestra visibile")


def _powershell_invocation(command: list[str]) -> str:
    """The command as a PowerShell call, quoted with the engine's own rule."""
    quoted = " ".join(powershell_literal(token) for token in command)
    return f"& {quoted}"


def _linux_window(title: str, command: list[str], folder: Path) -> tuple[str, tuple[list[str], dict]]:
    """The first graphical terminal installed; the shell stays after the command ends."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise WindowError(
            "nessuna sessione grafica: DISPLAY e WAYLAND_DISPLAY assenti, "
            "la finestra non si vedrebbe e il motore non esegue alla cieca"
        )
    terminal, title_flag, exec_flag = _linux_terminal()
    inner = ["bash", "-c", shlex.join(command) + "; exec bash"]
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
