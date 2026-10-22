"""The machine's process table: who is running, who hangs under whom, who is a shared server.

One job: list every process with its parent, its name, its command line and, on
Linux, its environment; build the subtree of a set of roots deepest first; stop
a list of pids; and recognise the `opencode serve` that every session shares.
Nothing here decides what may be killed: `supervision_windows` and
`supervision_cleanup` own that policy and call these primitives.

Windows asks CIM once per question. Linux reads `/proc`, which is the same fact
without a subprocess, and skips a process that exits while it is being read.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

from .supervision_state import SupervisionError


class ProcessError(SupervisionError):
    """The process table cannot be read, or a process cannot be stopped."""


#: How long one question about the process table may take.
PROCESS_TIMEOUT_S = 30.0
#: The shells a supervisor tab can be, on either platform.
SHELL_NAMES = frozenset({
    "pwsh", "pwsh.exe", "powershell", "powershell.exe",
    "bash", "sh", "dash", "zsh", "ksh", "fish",
})
_LIST_WINDOWS = (
    "Get-CimInstance Win32_Process | "
    "Select-Object ProcessId,ParentProcessId,Name,CommandLine,"
    "@{Name='Created';Expression={if ($_.CreationDate) {"
    "[int64]([System.DateTimeOffset]$_.CreationDate).ToUnixTimeSeconds()} else {0}}} | "
    "ConvertTo-Json -Compress"
)
_KILL_WINDOWS = "Stop-Process -Id {pids} -Force -ErrorAction SilentlyContinue"


def rows() -> list[dict]:
    """Every process on this machine: `pid`, `ppid`, `name`, `command_line`, `environ`."""
    return _windows_rows() if os.name == "nt" else _posix_rows()


def kill(pids: list[int]) -> None:
    """Stop these processes; one that is already gone is not a failure."""
    if not pids:
        return
    if os.name == "nt":
        _windows_kill(pids)
    else:
        _posix_kill(pids)


def cwd(pid: int) -> Path | None:
    """The directory a process works in, or None when the system will not say.

    This is how a server's child is tied to a copy: an MCP process started for a
    folder carries that folder as its current directory, and nothing else on the
    machine does. A process that exits while it is read, or one owned by another
    user, answers None and is never treated as belonging to anything.
    """
    if os.name == "nt":
        return _windows_cwd(pid)
    return _posix_cwd(pid)


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


def _windows_rows() -> list[dict]:
    """Every process, as CIM reports it."""
    output = _powershell(_LIST_WINDOWS)
    try:
        parsed = json.loads(output or "[]")
    except ValueError as exc:
        raise ProcessError(f"elenco dei processi illeggibile ({exc})") from exc
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [_windows_row(row) for row in parsed if isinstance(row, dict)]


def _windows_row(row: dict) -> dict:
    """One CIM row in the shape the rest of the engine uses."""
    found = {
        "pid": int(row.get("ProcessId") or 0),
        "ppid": int(row.get("ParentProcessId") or 0),
        "name": str(row.get("Name") or ""),
        "command_line": str(row.get("CommandLine") or ""),
        "environ": "",
    }
    created = _created_or_none(row.get("Created"))
    if created is not None:
        found["created"] = created
    return found


def _created_or_none(value) -> float | None:
    """A creation time as a number; a missing or zero one is not evidence."""
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _windows_kill(pids: list[int]) -> None:
    """Stop these processes through one PowerShell call."""
    _powershell(_KILL_WINDOWS.format(pids=",".join(str(pid) for pid in pids)))


def _powershell(script: str) -> str:
    """One PowerShell question, with the reason when it cannot answer."""
    try:
        done = subprocess.run(
            [powershell_executable(), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=PROCESS_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessError(f"PowerShell non ha risposto ({type(exc).__name__})") from exc
    if done.returncode:
        detail = (done.stderr or "").strip()[:200]
        raise ProcessError(f"PowerShell esce con {done.returncode}: {detail}")
    return done.stdout


def _posix_rows(root: Path | None = None) -> list[dict]:
    """Every process, as `/proc` reports it; `root` is for a test's own fake table."""
    base = root or Path("/proc")
    try:
        entries = list(base.iterdir())
    except OSError as exc:
        raise ProcessError(f"tabella dei processi non leggibile ({base}): {exc}") from exc
    found: list[dict] = []
    for entry in entries:
        if entry.name.isdigit():
            row = _posix_row(entry, int(entry.name))
            if row is not None:
                found.append(row)
    return found


def _posix_row(entry: Path, pid: int) -> dict | None:
    """One `/proc/<pid>` as a row, or None when the process exits while it is read."""
    try:
        stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        cmdline = (entry / "cmdline").read_bytes()
        environ = (entry / "environ").read_bytes()
    except OSError:  # nexgen: allow reason="un processo che esce mentre lo si legge non è un errore: si salta"
        return None
    parts = [part.decode("utf-8", "replace") for part in cmdline.split(b"\0") if part]
    row = {
        "pid": pid,
        "ppid": _ppid_from_stat(stat),
        "name": Path(parts[0]).name if parts else _comm_from_stat(stat),
        "command_line": " ".join(parts),
        "environ": environ.decode("utf-8", "replace"),
    }
    created = _starttime_from_stat(stat)
    if created is not None:
        row["created"] = created
    return row


def _ppid_from_stat(stat: str) -> int:
    """The parent pid from `/proc/<pid>/stat`, whose second field may hold spaces and parentheses."""
    after = stat.rsplit(")", 1)[-1].split()
    return int(after[1]) if len(after) > 1 and after[1].isdigit() else 0


def _comm_from_stat(stat: str) -> str:
    """The process name from `/proc/<pid>/stat`, for a process with an empty command line."""
    opening = stat.find("(")
    closing = stat.rfind(")")
    return stat[opening + 1:closing] if 0 <= opening < closing else ""


def _starttime_from_stat(stat: str) -> float | None:
    """Field 22 of `/proc/<pid>/stat`: when it started, in clock ticks since boot.

    The number is only compared inside one table, where every row shares the
    same boot and the same clock, so the ticks need no conversion.
    """
    after = stat.rsplit(")", 1)[-1].split()
    if len(after) <= 19 or not after[19].isdigit():
        return None
    return float(after[19])


def _posix_kill(pids: list[int]) -> None:
    """Stop these processes one by one; one that is already gone is not a failure."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:  # nexgen: allow reason="un processo già finito non è un errore: non c'è niente da fermare"
            continue
        except PermissionError as exc:
            raise ProcessError(f"permesso negato a fermare il processo {pid}") from exc


def _posix_cwd(pid: int) -> Path | None:
    """A process's working directory from `/proc`, which is a symlink to it."""
    try:
        found = os.readlink(f"/proc/{pid}/cwd")
    except OSError:  # nexgen: allow reason="un processo che esce mentre lo si legge non ha una cartella da attribuire"
        return None
    if found.endswith(" (deleted)"):  # nexgen: allow reason="una cartella cancellata sotto il processo non è la sua cwd"
        found = found[: -len(" (deleted)")]
    return Path(found) if found else None


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_VM_READ = 0x0010
    _NTDLL = ctypes.WinDLL("ntdll")
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _KERNEL32.OpenProcess.restype = wintypes.HANDLE
    _KERNEL32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _KERNEL32.ReadProcessMemory.restype = wintypes.BOOL
    _KERNEL32.IsWow64Process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    _KERNEL32.IsWow64Process.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
    _NTDLL.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint,
        ctypes.POINTER(ctypes.c_uint),
    ]
    _NTDLL.NtQueryInformationProcess.restype = ctypes.c_long

    class _ProcessBasicInformation(ctypes.Structure):
        """The one field the PEB read needs: where the process's PEB lives."""

        _fields_ = [
            ("Reserved1", ctypes.c_void_p),
            ("PebBaseAddress", ctypes.c_void_p),
            ("Reserved2", ctypes.c_void_p * 2),
            ("UniqueProcessId", ctypes.c_void_p),
            ("Reserved3", ctypes.c_void_p),
        ]

    def _windows_read(handle, address: int, size: int) -> bytes | None:
        """Read exactly this many bytes of another process, or None when Windows refuses."""
        buffer = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t()
        ok = _KERNEL32.ReadProcessMemory(
            handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read)
        )
        return buffer.raw if ok and read.value == size else None

    def _windows_cwd(pid: int) -> Path | None:
        """The PEB's current directory, which is where a child process was started.

        Only 64-bit processes are read: a 32-bit process keeps the same fact at
        other offsets, and guessing them would attribute the wrong folder to a
        process. A Windows process that cannot be opened answers None.
        """
        handle = _KERNEL32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ, False, pid
        )
        if not handle:
            return None
        try:
            wow64 = wintypes.BOOL()
            if _KERNEL32.IsWow64Process(handle, ctypes.byref(wow64)) and wow64.value:
                return None
            info = _ProcessBasicInformation()
            status = _NTDLL.NtQueryInformationProcess(
                handle, 0, ctypes.byref(info), ctypes.sizeof(info), None
            )
            if status != 0 or not info.PebBaseAddress:
                return None
            raw = _windows_read(handle, info.PebBaseAddress + 0x20, 8)
            if raw is None:
                return None
            parameters = int.from_bytes(raw, "little")
            head = _windows_read(handle, parameters + 0x38, 16)
            if head is None:
                return None
            length = int.from_bytes(head[:2], "little")
            buffer = int.from_bytes(head[8:16], "little")
            if not length or not buffer:
                return None
            data = _windows_read(handle, buffer, length)
            if data is None:
                return None
            text = data.decode("utf-16-le", "replace").rstrip("\x00")
            return Path(text) if text else None
        finally:
            _KERNEL32.CloseHandle(handle)
