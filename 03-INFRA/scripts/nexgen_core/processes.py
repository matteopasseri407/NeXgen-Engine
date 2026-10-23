"""Launch adapters and bounded cleanup for subprocess trees owned by us."""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from typing import Any


#: PowerShell reads all of these as a single quote, not just the ASCII apostrophe.
_POWERSHELL_QUOTES = "'\u2018\u2019\u201a\u201b"


def powershell_literal(text: str) -> str:
    """`text` as a PowerShell single-quoted string, safe to splice into a script.

    Doubling only the ASCII apostrophe let a typographic one end the string early: an Italian
    "l\u2019aggiornamento" in a notification body broke the script, and with text from outside
    (a commit subject, a path) that is injection, not just a lost toast.
    """
    return "'" + "".join(ch * 2 if ch in _POWERSHELL_QUOTES else ch for ch in text) + "'"


def split_command_line(command: str) -> list[str]:
    """A typed command line as argv for this platform.

    shlex in POSIX mode eats the backslashes of a Windows path (`C:\\tools` becomes
    `C: ools`), and the command then fails over a path that does not exist. The
    non-POSIX mode keeps them, but leaves the quotes inside the tokens, where
    subprocess would escape them again: one matching outer pair is peeled off.
    """
    argv = shlex.split(command, posix=os.name != "nt")
    if os.name != "nt":
        return argv
    return [
        token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'" else token
        for token in argv
    ]


def command_line_text(argv: list[str]) -> str:
    """An argv as a person would type it back: quoted where the shell needs it.

    `" ".join` printed `C:\\Program Files\\nodejs\\npm.cmd install ...`, which
    fails the moment it is pasted: the shell reads `C:\\Program` as the
    executable. Windows gets its own quoting, POSIX gets shlex's.
    """
    tokens = [str(token) for token in argv]
    return subprocess.list2cmdline(tokens) if os.name == "nt" else shlex.join(tokens)


def spawn_detached(argv: list[str]) -> int | None:
    """Start a long-lived child without waiting for it or keeping it a child.

    OpenClaw's Windows launcher is a `.vbs` whose `shell.Run(..., 0, True)`
    waits for `gateway.cmd` to exit. Waiting on it in `setup` meant the setup
    never reached the next step, and closing the terminal took the gateway
    with it. A detached process with its three streams on the null device is
    the gateway as a service, not the gateway as this command's child.
    """
    command = windows_command_argv(list(argv))
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs).pid


def windows_command_argv(argv: list[str]) -> list[str]:
    """Resolve npm command shims and invoke .cmd/.bat through cmd.exe."""
    if os.name != "nt" or not argv:
        return list(argv)
    executable = shutil.which(argv[0])
    if not executable:
        return list(argv)
    if executable.casefold().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/d", "/s", "/c", executable, *argv[1:]]
    # Anything else starts by name as well as by path: keeping the caller's
    # own argv means what a caller sees is what it asked for.
    return list(argv)


def no_window_kwargs() -> dict[str, Any]:
    """Popen/run kwargs that keep Windows from flashing a console.

    A console child of a process without its own console (desktop CLIs,
    MCP gateways, scheduled tasks) otherwise gets a visible window that
    steals focus on every spawn. No-op on POSIX.
    """
    if os.name != "nt":
        return {}
    kwargs: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    try:
        info = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
        info.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
        kwargs["startupinfo"] = info
    except (AttributeError, OSError):
        pass
    return kwargs


def force_stop_process_tree(
    proc: subprocess.Popen, *, process_group: int | None = None, timeout: float = 5.0,
) -> bool:
    """Kill owned descendants and reap the launcher within one cleanup budget.

    Only pass a POSIX group created by this caller with start_new_session.
    A parent can have exited while its children still own inherited pipes.
    Windows taskkill /T handles npm shims as well as direct executables.
    """
    # POSIX without a group has nothing left to signal; on Windows the
    # launcher can return while its children (npm shims, workers) stay
    # alive, so the tree kill below must still run.
    if os.name == "posix" and process_group is None and proc.poll() is not None:
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
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=min(3.0, remaining), check=False,
                    **no_window_kwargs(),
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
