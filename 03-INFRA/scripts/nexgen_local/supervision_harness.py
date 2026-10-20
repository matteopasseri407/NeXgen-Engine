"""The executor's harness: one session per task, its window, its prompt, its cost.

One harness means one session per task. A unit either enters the session the
task already has, when that session is idle, or waits in the queue while it is
busy; a new terminal tab is opened only when the task has no session yet. The
engine never pastes the brief into a command line, never picks a model, and
never reads what the terminal prints.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from nexgen_core.processes import no_window_kwargs, windows_command_argv

from . import supervision_costs as costs
from . import supervision_state as state
from . import supervision_windows as windows
from .supervision_state import SupervisionError

#: How long a new tab gets to open its session before the attach is left manual.
SESSION_WAIT_S = 30.0
POLL_S = 2.0
#: Clock slack between this process and the harness's own timestamps.
CREATED_SLACK_MS = 5000
LIST_TIMEOUT_S = 120.0

class HarnessError(SupervisionError):
    """The harness is missing, its session list is unreadable, or the unit has no copy to run in."""


def run_unit(
    task_id: str, unit: str, *, opener=None, runner=None, lister=None,
    measure=None, wait_s: float = SESSION_WAIT_S,
) -> dict:
    """Put the unit's work into the task's session, queue it, or open the first tab.

    The task has at most one session per harness: if it exists and is idle the
    work goes in with `opencode run -s`; if it is busy the unit is queued and
    says so; a tab is opened only when there is no session at all.
    """
    record = state.unit_record(task_id, unit)
    name = record["unit"]
    folder = Path(record.get("worktree") or "")
    if not folder.is_dir():
        raise HarnessError(f"copia dell'unità sparita: {folder}")
    brief = state.open_task(task_id) / f"20-brief-{name}.md"
    if not brief.is_file():
        raise HarnessError(f"brief dell'unità sparito: {brief}")
    prompt = prompt_text(task_id, name, brief)
    session = state.harness_session(task_id, "opencode")
    if not session:
        return _open_first_tab(task_id, name, folder, prompt, opener or windows.open_window, lister, wait_s)
    return _enter_the_session(task_id, name, session, folder, prompt, runner or _run_in_session, measure or costs.measure)


def prompt_text(task_id: str, unit: str, brief: Path) -> str:
    """A pointer to the brief, never the brief: the file stays the one place it is read from."""
    report = brief.parent / f"30-resoconto-{unit}.md"
    return (
        f"Sei l'esecutore dell'unità {unit} del compito {task_id}. "
        f"Leggi il brief in {brief} e segui solo quello. "
        f"Lavora in questa copia, committa sul ramo locale, scrivi il resoconto in {report}. "
        "Niente push."
    )


def find_session(
    folder: Path, since_ms: int, *, wait_s: float = SESSION_WAIT_S, lister=None,
    poll_s: float = POLL_S, clock=None, sleep=None,
) -> dict | None:
    """The newest session opened in `folder` at or after `since_ms`, or None when none appears."""
    wanted = _normal(folder)
    list_sessions = lister or _list_sessions
    now, pause = clock or time.monotonic, sleep or time.sleep
    deadline = now() + wait_s
    while True:
        matches = [
            item for item in list_sessions()
            if _normal(item.get("directory") or "") == wanted
            and int(item.get("created") or 0) >= since_ms - CREATED_SLACK_MS
        ]
        if matches:
            return max(matches, key=lambda item: int(item.get("created") or 0))
        if now() >= deadline:
            return None
        pause(poll_s)


def _enter_the_session(task_id: str, unit: str, session: str, folder: Path, prompt: str, runner, measure) -> dict:
    """Send the unit's work into the task's own session, or queue it when that session is busy."""
    try:
        facts = measure(session)
    except costs.CostError as exc:
        state.append_event(
            task_id, "unit_queued", unit=unit, session=session,
            reason=f"stato della sessione non leggibile: {exc}",
        )
        return {"unit": unit, "mode": "queued", "session": session, "reason": str(exc)}
    if not facts.stopped:
        reason = "sessione occupata"
        state.append_event(task_id, "unit_queued", unit=unit, session=session, reason=reason)
        return {"unit": unit, "mode": "queued", "session": session, "reason": reason}
    proc = runner(session, prompt, folder)
    state.append_event(
        task_id, "unit_started", unit=unit, session=session, mode="run",
        pid=getattr(proc, "pid", None),
    )
    row = costs.record(task_id, unit, "esecutore", session, window=windows.WINDOW_NAME)
    return {"unit": unit, "mode": "run", "session": session, "pid": getattr(proc, "pid", None), "row": row}


def _open_first_tab(task_id: str, unit: str, folder: Path, prompt: str, opener, lister, wait_s: float) -> dict:
    """No session for this harness yet: open the tab and bind the session it creates."""
    started_ms = int(time.time() * 1000)
    handle = opener(f"esecutore · {unit}", [_opencode_executable(), str(folder), "--prompt", prompt], cwd=folder)
    state.append_event(task_id, "unit_started", unit=unit, window=str(handle.get("title", "")), mode="tab")
    session = find_session(folder, started_ms, wait_s=wait_s, lister=lister)
    if session is not None:
        _attach(task_id, unit, session, handle)
    return {"unit": unit, "mode": "tab", "window": handle, "session": session}


def _run_in_session(session: str, prompt: str, folder: Path):
    """One-shot work inside the task's session; the terminal shows it, the engine does not watch."""
    argv = windows_command_argv([_opencode_executable(), "run", "-s", session, prompt])
    try:
        return subprocess.Popen(
            argv, cwd=folder,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **(_detach_kwargs()),
        )
    except OSError as exc:
        raise HarnessError(f"sessione {session} non raggiungibile: {exc}") from exc


def _detach_kwargs() -> dict:
    """Let the harness outlive the command that started it."""
    if sys.platform.startswith(("win", "cygwin", "msys")):
        return no_window_kwargs()
    return {"start_new_session": True}


def _attach(task_id: str, unit: str, session: dict, handle: dict) -> None:
    """Write the cost row and the journal event for the session the tab opened."""
    session_id = str(session.get("id") or "")
    row = costs.record(task_id, unit, "esecutore", session_id, window=str(handle.get("title", "")))
    state.append_event(
        task_id, "attached",
        unit=unit, role="esecutore", harness="opencode", session=row["session"],
    )


def _list_sessions() -> list[dict]:
    """Every session the harness knows, newest first, as the harness reports it."""
    executable = _opencode_executable()
    argv = windows_command_argv([executable, "session", "list", "--format", "json", "-n", "100"])
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=LIST_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError(f"`opencode session list` non eseguibile ({type(exc).__name__})") from exc
    if done.returncode:
        raise HarnessError(f"`opencode session list` esce con {done.returncode}")
    try:
        payload = json.loads(done.stdout)
    except ValueError as exc:
        raise HarnessError("`opencode session list` non è JSON") from exc
    if not isinstance(payload, list):
        raise HarnessError("`opencode session list` non è una lista di sessioni")
    return [item for item in payload if isinstance(item, dict)]


def _opencode_executable() -> str:
    return costs.opencode_executable()


def _normal(path: str | Path) -> str:
    """A folder as one comparable string, case and separators aside."""
    return str(Path(path)).replace("\\", "/").rstrip("/").casefold()
