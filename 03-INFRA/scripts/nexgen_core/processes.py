"""Launch adapters and bounded cleanup for subprocess trees owned by us."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time


#: PowerShell reads all of these as a single quote, not just the ASCII apostrophe.
_POWERSHELL_QUOTES = "'\u2018\u2019\u201a\u201b"


def powershell_literal(text: str) -> str:
    """`text` as a PowerShell single-quoted string, safe to splice into a script.

    Doubling only the ASCII apostrophe let a typographic one end the string early: an Italian
    "l\u2019aggiornamento" in a notification body broke the script, and with text from outside
    (a commit subject, a path) that is injection, not just a lost toast.
    """
    return "'" + "".join(ch * 2 if ch in _POWERSHELL_QUOTES else ch for ch in text) + "'"


def windows_command_argv(argv: list[str]) -> list[str]:
    """Resolve npm command shims and invoke .cmd/.bat through cmd.exe."""
    if os.name != "nt" or not argv:
        return list(argv)
    executable = shutil.which(argv[0])
    if not executable:
        return list(argv)
    if executable.casefold().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/d", "/s", "/c", executable, *argv[1:]]
    return [executable, *argv[1:]]


def force_stop_process_tree(
    proc: subprocess.Popen, *, process_group: int | None = None, timeout: float = 5.0,
) -> bool:
    """Kill owned descendants and reap the launcher within one cleanup budget.

    Only pass a POSIX group created by this caller with start_new_session.
    A parent can have exited while its children still own inherited pipes.
    Windows taskkill /T handles npm shims as well as direct executables.
    """
    if process_group is None and proc.poll() is not None:
        return True
    deadline = time.monotonic() + max(0.0, timeout)
    tree_killed = False
    if os.name == "posix" and process_group is not None:
        try:
            os.killpg(process_group, signal.SIGKILL)
            tree_killed = True
        except OSError:
            pass
    elif os.name == "nt" and getattr(proc, "pid", None) is not None:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                result = subprocess.run(
                    ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=min(3.0, remaining), check=False,
                )
                tree_killed = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
    if not tree_killed:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    except TypeError:  # compatibility with lightweight Council test doubles
        proc.wait()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True
