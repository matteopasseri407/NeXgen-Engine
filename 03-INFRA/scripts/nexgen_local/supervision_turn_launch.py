"""Start one harness turn with no window and make sure nothing it started outlives it.

One job: launch the one-shot command an adapter builds, detached and windowless,
put it in the unit's job object, and hand it to a guardian process that waits
for the turn to end or time out and then stops every process the turn left
behind. Before this module a finished `opencode run` could leave MCP servers and
shells running until reboot, and a turn that hung was never stopped; the
supervisor returns at once, so the waiting has to live in a process of its own.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from nexgen_core.processes import no_window_kwargs, windows_command_argv

from . import supervision_job as jobs
from . import supervision_policy as policy
from . import supervision_reaper as reaper
from . import supervision_state as state
from .supervision_state import SupervisionError


class TurnLaunchError(SupervisionError):
    """The harness's command could not be started at all."""


def stderr_log(folder: Path, session: str) -> Path:
    """The file a turn's own errors go to, beside the copy and inside the task folder.

    A turn that dies before its envelope says why on stderr and nowhere else, and a
    silent death is the one thing the supervisor cannot read afterwards. The copy is
    `<task>/23-unita-<unit>/`, so the parent is the task folder the owner opens.
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in session)[:80]
    return folder.parent / f"60-stderr-{safe or 'session'}.log"


def run_turn(adapter, executable: str, session: str, prompt: str, folder: Path, model: str = "",
             *, first: bool = False):
    """One turn through this harness, detached: no window of the engine is left holding it.

    `first` is the turn that opens the unit's session, built with the adapter's
    headless form; every later turn goes into the session with `send_argv`. The
    copy is checked first, because one harness runs its turn in a workspace the
    owner configured and a wrong folder would look like work in the unit. A
    harness whose numbers are only in what the turn prints writes them into its
    own log; whatever the turn says on stderr is kept beside the copy.
    """
    adapter.check_folder(folder)
    built = (
        adapter.headless_argv(executable, folder, prompt, session=session, model=model)
        if first else adapter.send_argv(executable, session, prompt, model=model)
    )
    argv = windows_command_argv(built)
    extra = adapter.turn_env(model) if hasattr(adapter, "turn_env") else {}
    env = turn_environment(folder, extra)
    log = adapter.turn_log(session)
    errors = stderr_log(folder, session)
    try:
        errors.parent.mkdir(parents=True, exist_ok=True)
        with errors.open("a", encoding="utf-8", newline="\n") as err:
            if log is None:
                return _start(argv, folder, subprocess.DEVNULL, err, env)
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8", newline="\n") as handle:
                return _start(argv, folder, handle, err, env)
    except OSError as exc:
        raise TurnLaunchError(f"sessione {session} non raggiungibile: {exc}") from exc


def turn_environment(folder: Path, extra: dict[str, str]) -> dict[str, str]:
    """The caller's environment, the adapter's additions, and `PWD` set to the unit's copy.

    `cwd` moves the process, not the variable a shell exported: OpenCode reads
    `PWD` to choose the session's folder, so a `sup run` started from a Git Bash
    in another checkout opened the session there and the worker committed into
    that checkout (seen live on 2026-11-25). Every turn now names its copy twice.
    """
    return {**os.environ, **extra, "PWD": str(folder)}


def _start(argv: list[str], folder: Path, stdout, stderr, env: dict[str, str]):
    """The process itself, in the copy, with the environment that names the copy."""
    return subprocess.Popen(
        argv, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
        **detach_kwargs(),
    )


def hold_turn(task_id: str, unit: str, proc) -> None:
    """Put this turn inside the unit's job object and under its guardian.

    The job is what lets stopping the unit end the whole tree in one kernel call;
    the guardian is what ends the tree when the turn finishes or runs out of time.
    Either one that cannot be had is journalled (`unit_unjobbed`,
    `turn_unguarded`) instead of pretended: the turn still runs.
    """
    pid = getattr(proc, "pid", None)
    name = jobs.job_name(task_id, unit)
    note = jobs.adopt(name, pid)
    if note:
        state.append_event(task_id, "unit_unjobbed", unit=unit, pid=pid, reason=note)
    if not reaper.watchable(proc):
        return
    refused = reaper.watch(
        task_id, unit, proc, timeout_s=policy.turn_timeout_minutes() * 60,
        job=name if jobs.available() else "",
    )
    if refused:
        state.append_event(task_id, "turn_unguarded", unit=unit, pid=pid, reason=refused)


def detach_kwargs() -> dict:
    """Let the harness outlive the command that started it, in a group of its own."""
    if sys.platform.startswith(("win", "cygwin", "msys")):
        return no_window_kwargs()
    return {"start_new_session": True}
