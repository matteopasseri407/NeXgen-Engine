"""The machine's process table: who is running, who hangs under whom, who is a shared server.

One job: list every process with its parent, its name, its command line and, on
Linux, its environment; build the subtree of a set of roots deepest first; stop
a list of pids; run one command with its output captured through a file so a
stuck child cannot hang the caller; and recognise the `opencode serve` that every
session shares. Nothing here decides what may be killed: `supervision_windows` and
`supervision_cleanup` own that policy and call these primitives.

One mechanism for both platforms: psutil reads the table, the creation time, the
command line, the environment and the working directory and stops a process, so
the engine keeps no `/proc` reader and asks no CIM question per lookup. A process
that dies, belongs to another user or is already a zombie is a normal answer
here, each one named on its own `except` line.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import psutil

from nexgen_core.processes import force_stop_process_tree, no_window_kwargs, windows_command_argv

from .supervision_state import SupervisionError


class ProcessError(SupervisionError):
    """The process table cannot be read, or a process cannot be stopped."""


#: The shells a supervisor tab can be, on either platform.
SHELL_NAMES = frozenset({
    "pwsh", "pwsh.exe", "powershell", "powershell.exe",
    "bash", "sh", "dash", "zsh", "ksh", "fish",
})
#: What one row needs from psutil. The parent is not among them: on Windows psutil
#: answers `ppid` with a whole-system snapshot per process, which costs a minute on a
#: machine running a thousand of them, so the whole table is built from one snapshot
#: instead (`_parent_map`). The environment is asked for on Linux only: a Windows
#: process this user may not open answers nothing useful there, and no tab marker is
#: looked for in it (`supervision_windows` reads the assignment in the command line).
_ROW_ATTRS: tuple[str, ...] = ("pid", "name", "cmdline", "create_time") + (
    () if os.name == "nt" else ("environ",)
)
#: What the kernel appends to a working directory that was removed under the process.
_DELETED_SUFFIX = " (deleted)"


def rows() -> list[dict]:
    """Every process on this machine: `pid`, `ppid`, `name`, `command_line`, `environ`."""
    try:
        found = list(psutil.process_iter(_ROW_ATTRS, ad_value=None))
        parents = _parent_map(found)
    except (psutil.Error, OSError) as exc:
        raise ProcessError(f"tabella dei processi non leggibile ({type(exc).__name__}: {exc})") from exc
    return [_row(proc.info, parents.get(proc.pid, 0)) for proc in found]


def _parent_map(procs: list) -> dict[int, int]:
    """Every pid's parent, from one snapshot where psutil offers one.

    On Windows `Process.ppid()` is a whole-system query per call, and a table of a
    thousand processes then takes a minute; psutil's own snapshot answers the same
    question once for every pid, and it is the one its `children()` walks. Everywhere
    else the parent is one small file read per process, and the plain call is cheap.
    """
    snapshot = getattr(psutil, "_pswindows", None) if os.name == "nt" else None
    bulk = getattr(snapshot, "ppid_map", None)
    if bulk is not None:
        return dict(bulk())
    found: dict[int, int] = {}
    for proc in procs:
        try:
            found[proc.pid] = proc.ppid()
        except psutil.NoSuchProcess:  # nexgen: allow reason="un processo che esce mentre si legge la tabella non ha un padre da riportare, e la sua riga resta senza"
            continue
    return found


def kill(pids: list[int]) -> None:
    """Stop these processes; one that is already gone is not a failure."""
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:  # nexgen: allow reason="un processo già finito, e uno zombie, non è un errore: non c'è niente da fermare"
            continue
        except psutil.AccessDenied as exc:
            raise ProcessError(f"permesso negato a fermare il processo {pid}") from exc


def captured(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Run one command and read its output from a file, so a stuck child cannot hang the caller.

    An npm shim starts a Node child. On the timeout the rerun path kills the
    shim, and the child can outlive it still holding the pipe: a pipe-based
    reader then waits for ever on a command nobody is writing to. The output goes
    to a temporary file instead, the timeout stops the whole tree, and the caller
    gets what was written or the timeout, never a hang.
    """
    prepared = windows_command_argv(argv)
    with tempfile.TemporaryFile("w+b") as out:
        try:
            proc = subprocess.Popen(
                prepared, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                **no_window_kwargs(),
            )
        except OSError as exc:
            raise ProcessError(f"`{argv[0]}` non eseguibile ({type(exc).__name__})") from exc
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            force_stop_process_tree(proc)
            raise ProcessError(f"`{argv[0]}` non risponde in {timeout:.0f}s") from exc
        out.seek(0)
        text = out.read().decode("utf-8", "replace")
    return subprocess.CompletedProcess(prepared, code, text, "")


def cwd(pid: int) -> Path | None:
    """The directory a process works in, or None when the system will not say.

    This is how a server's child is tied to a copy: an MCP process started for a
    folder carries that folder as its current directory, and nothing else on the
    machine does. A process that exits while it is read, or one owned by another
    user, answers None and is never treated as belonging to anything.
    """
    try:
        found = psutil.Process(pid).cwd()
    except psutil.NoSuchProcess:  # nexgen: allow reason="un processo che esce, o già zombie, non ha una cartella da attribuire, e il contratto di questa funzione è None"
        return None
    except psutil.AccessDenied:  # nexgen: allow reason="un processo di un altro utente non si guarda: la funzione risponde None invece di inventare una cartella"
        return None
    if found.endswith(_DELETED_SUFFIX):  # nexgen: allow reason="una cartella cancellata sotto il processo non è la sua cwd"
        found = found[: -len(_DELETED_SUFFIX)]
    return Path(found) if found else None


def subtree(table: list[dict], roots: list[int]) -> list[tuple[int, dict]]:
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
        started = (node or {}).get("created")
        for child in by_parent.get(pid, []):
            if _created_before(child, started):
                continue
            stack.append((child["pid"], depth + 1))
    return sorted(found, key=lambda item: item[0], reverse=True)


def _created_before(child: dict, parent_created: float | None) -> bool:
    """Whether this child was born before its presumed parent: a reused pid, not a child.

    Windows keeps `ParentProcessId` of a dead parent even after the pid is
    assigned to another process, so the parent link alone can pull an unrelated
    tree into the kill set. A child cannot predate its parent; when either
    creation time is unknown the link is kept rather than guessed away.
    """
    started = child.get("created")
    if not isinstance(started, (int, float)) or not isinstance(parent_created, (int, float)):
        return False
    return started < parent_created


def is_shared_server(row: dict) -> bool:
    """Whether this row is the `opencode serve` that every session on the machine shares.

    `serve` has to be the first argument, not a word somewhere in the command
    line: a tab's prompt that happens to mention it is not the server, and
    refusing to close every tab that talks about serving would be a different bug.
    """
    name = Path(str(row.get("name") or "")).name.lower()
    if not name.startswith("opencode"):
        return False
    return "serve" in _first_arguments(str(row.get("command_line") or ""))


def _first_arguments(command_line: str, count: int = 2) -> list[str]:
    """The first words of a command line, with the executable's quotes taken off."""
    found: list[str] = []
    for token in re.findall(r'"[^"]*"|\S+', command_line):
        found.append(token.strip('"'))
        if len(found) == count:
            break
    return found


def is_shell(row: dict) -> bool:
    """Whether this row is one of the shells a supervisor tab can be."""
    return Path(str(row.get("name") or "")).name.lower() in SHELL_NAMES


def has_env(row: dict, entry: str) -> bool:
    """Whether the process's environment carries exactly this `NAME=value` entry."""
    environ = str(row.get("environ") or "")
    return bool(environ) and entry in environ.split("\0")


def powershell_executable() -> str:
    """Windows PowerShell 7 when present, the one Windows always has otherwise."""
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    raise ProcessError("PowerShell non trovato: impossibile aprire una finestra visibile")


def _row(info: dict, ppid: int) -> dict:
    """One process as psutil describes it, in the shape the rest of the engine reads."""
    found = {
        "pid": int(info.get("pid") or 0),
        "ppid": int(ppid or 0),
        "name": str(info.get("name") or ""),
        "command_line": " ".join(info.get("cmdline") or ()),
        "environ": _environ_text(info.get("environ")),
    }
    created = _created_or_none(info.get("create_time"))
    if created is not None:
        found["created"] = created
    return found


def _environ_text(environ) -> str:
    """The environment in the form the marker rule reads: `NAME=value` entries separated by NUL."""
    if not isinstance(environ, dict):
        return ""
    return "".join(f"{name}={value}\0" for name, value in environ.items())


def _created_or_none(value) -> float | None:
    """A creation time as a number; a missing or zero one is not evidence."""
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None
