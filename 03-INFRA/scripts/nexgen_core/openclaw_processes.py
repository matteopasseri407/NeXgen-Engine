"""Which running processes are our OpenClaw gateway, and how to launch one.

One job: the process side of the service. `openclaw_service` decides what to do
with a gateway (stop it, start it, wait for its port); this module answers what
a gateway process looks like on each platform, and runs a short command whose
output must be captured. Splitting it keeps the lifecycle file readable and
gives the "is this really our gateway" rule one home, which is what a mistaken
`taskkill` on a stranger's `gateway.cmd` taught.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from nexgen_core.processes import no_window_kwargs


@dataclass
class RunResult:
    """Execution output from a child process."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class ProcessInfo:
    """Descriptor for an enumerated system process."""

    pid: int
    name: str = ""
    cmdline: str = ""


def is_gateway_process(proc: ProcessInfo) -> bool:
    """Check whether a running process is an OpenClaw gateway instance."""
    name = proc.name.lower()
    cmd = proc.cmdline.lower()
    is_node = "node" in name
    is_openclaw = "openclaw" in cmd
    is_gateway = "dist/index.js gateway" in cmd or "dist\\index.js gateway" in cmd or "index.js gateway" in cmd
    if is_node and is_openclaw and is_gateway:
        return True
    is_cmd = "cmd" in name
    if is_cmd and "openclaw" in cmd and "gateway.cmd" in cmd:
        return True
    return False


def default_runner(argv: list[str]) -> RunResult:
    """Run an external command capturing stdout and stderr."""
    executable = shutil.which(argv[0]) if argv else None
    cmd = [executable, *argv[1:]] if executable else argv
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **no_window_kwargs(),
        )
    except OSError as exc:  # nexgen: allow reason="a program that is not there is an answer the caller reports, not a traceback that ends the install"
        return RunResult(127, "", f"{argv[0] if argv else ''}: {exc}")
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


def default_windows_process_lister() -> list[ProcessInfo]:
    """Enumerate running processes on Windows using PowerShell Get-CimInstance."""
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId, Name, CommandLine | "
        "ConvertTo-Json -Compress"
    )
    res = default_runner(["powershell.exe", "-NoProfile", "-Command", script])
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        data = json.loads(res.stdout)
        items = data if isinstance(data, list) else [data]
        return [
            ProcessInfo(
                pid=int(item.get("ProcessId") or 0),
                name=str(item.get("Name") or ""),
                cmdline=str(item.get("CommandLine") or ""),
            )
            for item in items
            if item.get("ProcessId")
        ]
    except (json.JSONDecodeError, ValueError, TypeError):  # nexgen: allow reason="unparseable process output defaults to empty list"
        return []


def default_linux_process_lister() -> list[ProcessInfo]:
    """Enumerate running processes on Linux using ps."""
    res = default_runner(["ps", "-eo", "pid,comm,args"])
    if res.returncode != 0:
        return []
    procs: list[ProcessInfo] = []
    for line in res.stdout.splitlines()[1:]:
        parts = line.strip().split(maxsplit=2)
        if len(parts) >= 2:
            try:
                pid = int(parts[0])
                name = parts[1]
                cmdline = parts[2] if len(parts) > 2 else name
                procs.append(ProcessInfo(pid=pid, name=name, cmdline=cmdline))
            except ValueError:  # nexgen: allow reason="non-integer pid line is skipped"
                continue
    return procs
