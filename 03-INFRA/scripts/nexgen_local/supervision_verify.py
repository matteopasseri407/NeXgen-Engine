"""The executor's declared verification, run by the engine with the engine's own launch rules.

One job: take one command and run it inside the unit's copy without letting the
copy decide what runs. Two things are refused before anything starts. The
command is one command, not a shell line: `&&`, `||`, `|`, `;` and `&` are not
supported, and a verifier that needs several steps writes a script. And the
executable must not come from the copy itself: Windows looks in the working
directory before PATH, so a `pytest.bat` dropped there would answer for the real
runner. A Python interpreter is started with `-P`, because without it
`python -m pytest` puts the working directory first on `sys.path` and imports the
copy's own `pytest.py`, which is the same bypass by another name. `-P` does not
touch `PYTHONPATH`, so the environment is cleaned as well.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from nexgen_core.processes import no_window_kwargs, split_command_line, windows_command_argv

from . import supervision_pytest as pytest_runs

#: The declared budget for one run of the executor's verification command.
TIMEOUT_S = 600.0
#: Shell operators the verification command may not carry: it is one command, not a script.
SHELL_OPERATORS = ("&&", "||", "|", ";", "&")
#: Suffixes Windows tries for a bare name, in the order the loader tries them.
WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")
#: A Python interpreter by name; its `-m` would put the working directory on `sys.path`.
PYTHON_EXECUTABLE = re.compile(r"^python(\d+(\.\d+)*)?(\.exe)?$", re.IGNORECASE)
PYTHON_LAUNCHER = re.compile(r"^py(\.exe)?$", re.IGNORECASE)


def run(folder: Path, command: str) -> tuple[bool, str, str]:
    """Whether the command ran and exited clean: the verdict, one line of reason, the output."""
    if not command.strip():
        return False, "nessun comando di verifica dichiarato: senza, il motore non può dire verificato", ""
    argv = split_command_line(command)
    shell = [token for token in argv if token in SHELL_OPERATORS]
    if shell:
        return False, (
            f"il comando di verifica contiene {', '.join(shell)}: non passa da una shell. "
            "Scrivi uno script o un solo comando"
        ), ""
    inside = executable_inside(argv, folder)
    if inside is not None:
        return False, (
            f"l'eseguibile `{argv[0]}` della verifica sta dentro la copia dell'unità ({inside}): "
            "un esecutore non può fornire il proprio verificatore. Usa un interprete o un runner fuori dalla copia"
        ), ""
    try:
        done = subprocess.run(
            windows_command_argv(launch_argv(argv)), cwd=folder, capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=pytest_runs.cleaned_environment(),
            timeout=TIMEOUT_S, check=False, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return False, f"tempo massimo superato ({TIMEOUT_S:.0f}s)", ""
    except OSError as exc:
        return False, f"comando non avviato ({type(exc).__name__}: {exc})", ""
    output = (done.stdout + done.stderr).strip()
    if done.returncode:
        return False, f"`{command}` esce con {done.returncode}: {_last_line(output)}", output
    return True, f"`{command}` esce con 0", output


def launch_argv(argv: list[str]) -> list[str]:
    """The verification argv, with `-P` when it starts a Python interpreter.

    Python 3.11 answers `-P` with "do not prepend the working directory to
    `sys.path`". The engine's own Python requirement is 3.11, so the flag is
    always available where the engine runs.
    """
    if not argv:
        return []
    name = Path(argv[0]).name
    if PYTHON_EXECUTABLE.match(name) or (os.name == "nt" and PYTHON_LAUNCHER.match(name)):
        return [argv[0], "-P", *argv[1:]]
    return list(argv)


def executable_inside(argv: list[str], folder: Path) -> Path | None:
    """The file the engine would run when the unit's own copy provides it, or None."""
    if not argv:
        return None
    name = argv[0]
    candidate = Path(name)
    root = folder.resolve()
    if candidate.is_absolute() or len(candidate.parts) > 1:
        resolved = candidate if candidate.is_absolute() else (folder / candidate)
        return resolved if within(resolved, root) else None
    local = [folder / name]
    if os.name == "nt":
        local += [folder / f"{name}{suffix}" for suffix in WINDOWS_EXECUTABLE_SUFFIXES]
    for path in local:
        if path.is_file():
            return path
    found = shutil.which(name)
    return Path(found) if found and within(Path(found), root) else None


def within(path: Path, root: Path) -> bool:
    """True when `path` is inside `root`, with a different drive counting as outside."""
    return Path(path).resolve().is_relative_to(root)


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:200]
