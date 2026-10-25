"""Running one command for the node transition, on the platform it runs on.

One job: start the OpenClaw CLI (and the platform commands the transition needs)
without a command interpreter, and give every call the deadline its work
deserves. Parsing stays in `node_status`, decisions stay in `node_transition`.

Two measured facts decided this file. On Windows `openclaw` on PATH is npm's
`.cmd` shim, and Windows starts a batch file only by handing it to `cmd.exe`,
which re-reads `&`, `|`, `^` and `%` inside every argument; npm puts the
JavaScript the shim wraps beside it, and the rule that starts that with node
already lives in `nexgen_core.processes`, so this module asks for it instead of
inventing a second one. And a cold start of OpenClaw on Windows answers in
fifteen to twenty-five seconds, so the ten-second ceiling the transition used
before reported a node that was working as unreadable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.megaphone import OPENCLAW_COMMAND, openclaw_launch
from nexgen_core.openclaw_service import RunResult
from nexgen_core.processes import no_window_kwargs

#: A question about state. Its answer can be slow on a cold start, and the person
#: waits for it: this is the ceiling that keeps a hang from being forever, not a guess.
READ_TIMEOUT_S = 60.0

#: Changing a service outlasts a question, because it installs, stops or removes a
#: unit. Still bounded: a command that never returns must not own the terminal.
SERVICE_TIMEOUT_S = 180.0

#: `name -> path or None`: where a command is. Injectable so a test never starts OpenClaw.
Which = Callable[[str], "str | None"]

#: `(argv, timeout) -> RunResult`: the executor, injectable for the same reason.
Run = Callable[[list[str], float], RunResult]


def launch_argv(argv: list[str], which: Which | None = None) -> list[str] | None:
    """The argv that starts this command here; None when it is installed but cannot start.

    Only the OpenClaw command is rewritten: `schtasks.exe` and `systemctl` start by
    name as the caller gave them. A command that is not installed stays a bare name,
    so the caller reads "not installed" rather than "broken"; the one case that
    returns None is npm's shim sitting there without the entry it wraps.
    """
    if not argv or Path(argv[0]).stem.casefold() != OPENCLAW_COMMAND:
        return list(argv)
    lookup = which or shutil.which
    found = lookup(argv[0])
    if not found:
        return list(argv)
    launch = openclaw_launch(found, lookup)
    if launch is None:
        return None
    return [*launch, *argv[1:]]


def run_command(argv: list[str], timeout: float, which: Which | None = None) -> RunResult:
    """Run one command once and bring back its output; a failure is a sentence, never a raise.

    `timeout` is passed in by the caller because only the caller knows whether it is
    asking a question or changing a service. A test that forgets to inject its own
    recorder gets nothing run at all: `openclaw gateway stop` is not something to
    execute by accident on the machine the tests are written on.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return RunResult(1, "", "test mode: real command execution prevented")
    launch = launch_argv(argv, which)
    if launch is None:
        return RunResult(127, "", t(
            "openclaw installato ma non avviabile: manca node oppure il file di avvio del pacchetto"))
    try:
        res = subprocess.run(
            launch,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            **no_window_kwargs(),
        )
    except FileNotFoundError:
        return RunResult(127, "", t("eseguibile {name} non trovato", name=argv[0]))
    except subprocess.TimeoutExpired:
        return RunResult(1, "", t("{command} non ha risposto entro {seconds} secondi",
                                  command=" ".join(argv), seconds=int(timeout)))
    except OSError as exc:
        return RunResult(1, "", t("{command} non avviabile: {error}", command=argv[0], error=exc))
    return RunResult(res.returncode, res.stdout, res.stderr)
