"""Visible terminal windows for a supervised unit: open one, never hide it.

A supervised job is only trustworthy if the owner can watch it: every harness
gets a real window that runs its command and stays open at the end, and the
engine never reads that window's output. When a window cannot be opened this
module raises with the reason instead of falling back to a hidden process,
which is exactly the silent execution it exists to prevent.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from nexgen_core.processes import powershell_literal

from .supervision_state import SupervisionError


class WindowError(SupervisionError):
    """No graphical session, no terminal installed, no shell to keep the window open."""


#: Linux terminals, first found wins; each wants the title and the command differently.
_LINUX_TERMINALS: tuple[tuple[str, str, str], ...] = (
    ("x-terminal-emulator", "-T", "-e"),
    ("gnome-terminal", "--title", "--"),
    ("konsole", "--title", "-e"),
    ("xterm", "-T", "-e"),
)


def open_window(title: str, argv: Sequence[str], *, cwd: str | Path) -> dict:
    """Open a visible terminal that runs `argv` and stays open; return what started.

    Raises ``WindowError`` when the platform cannot show one. Nothing is ever
    launched hidden: every refusing branch raises before a process starts.
    """
    command = [str(token) for token in argv]
    if not command:
        raise WindowError("comando vuoto: una finestra senza comando non serve a niente")
    folder = Path(cwd)
    if not folder.is_dir():
        raise WindowError(f"cartella di lavoro inesistente: {folder}")
    if os.name == "nt":
        launcher, spawned = _windows_window(title, command, folder)
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
    }


def _windows_window(title: str, command: list[str], folder: Path) -> tuple[str, tuple[list[str], dict]]:
    """Windows Terminal when installed, else a dedicated console; both stay open."""
    shell = _powershell_executable()
    script = _powershell_invocation(command)
    terminal = shutil.which("wt.exe")
    if terminal:
        argv = [
            terminal, "-w", "-1", "new-tab",
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
