"""The executor's harness: one session per task, its window, its prompt, its cost.

One harness means one session per task. A unit either enters the session the
task already has, when that session is idle, or waits in the queue while it is
busy; a new terminal tab is opened only when the task has no session yet, and a
review's corrections always go into the session that already did the work. The
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
from . import supervision_permissions as permissions
from . import supervision_policy as policy
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


class SessionLost(HarnessError):
    """The task's session is gone from the harness: deleted, pruned, or unreadable."""

    def __init__(self, session: str, reason: str) -> None:
        super().__init__(f"la sessione {session} non esiste più: {reason}")
        self.session = session
        self.reason = reason


def run_unit(
    task_id: str, unit: str, *, opener=None, runner=None, lister=None,
    measure=None, wait_s: float = SESSION_WAIT_S,
) -> dict:
    """Put the unit's work into the task's session, queue it, or open the first tab.

    The task has at most one session per harness: if it exists and is idle the
    work goes in with `opencode run -s`; if it is busy the unit is queued and
    says so; a tab is opened only when there is no session at all, or when the
    session the journal names no longer exists in the harness.
    """
    record, folder = _unit_copy(task_id, unit)
    name = record["unit"]
    brief = state.open_task(task_id) / f"20-brief-{name}.md"
    if not brief.is_file():
        raise HarnessError(f"brief dell'unità sparito: {brief}")
    permissions.write_profile(task_id, folder)
    prompt = prompt_text(task_id, name, brief)
    allowed = policy.allowed_models()
    session = state.harness_session(task_id, "opencode")
    if not session:
        return _open_first_tab(
            task_id, name, folder, prompt, opener or windows.open_window, lister, wait_s,
            measure=measure, allowed=allowed,
        )
    return _deliver(
        task_id, name, folder, prompt, session, event="unit_started", extra={},
        opener=opener, runner=runner, lister=lister, measure=measure, wait_s=wait_s,
        allowed=allowed,
    )


def send_corrections(
    task_id: str, unit: str, review: str | Path, round_no: int, *,
    runner=None, measure=None, opener=None, lister=None, wait_s: float = SESSION_WAIT_S,
    open_without_session: bool = False,
) -> dict:
    """Put a review round's corrections into the unit's session, or queue them while it is busy.

    The corrections stay in their file: the command line carries a pointer, the
    same way `sup run` points at the brief. Without a session there is nothing
    to correct, and opening a window on a unit nobody ran would be a lie; a
    session that disappeared is a different thing, and gets the one new tab.
    `open_without_session` is for `sup review --resend`: a round that never
    reached anybody may open the first tab exactly as `sup run` does.
    """
    record, folder = _unit_copy(task_id, unit)
    name = record["unit"]
    path = Path(review)
    if not path.is_file():
        raise HarnessError(f"file di revisione sparito: {path}")
    permissions.write_profile(task_id, folder)
    prompt = corrections_text(task_id, name, path, round_no)
    allowed = policy.allowed_models()
    session = state.harness_session(task_id, "opencode")
    if not session:
        if not open_without_session:
            raise HarnessError(
                f"nessuna sessione harness sul compito: la correzione dell'unità {name} non ha "
                "dove arrivare (`sup run` apre la prima sessione)"
            )
        return _open_first_tab(
            task_id, name, folder, prompt, opener or windows.open_window, lister, wait_s,
            event="unit_reworked", extra={"round": round_no}, measure=measure, allowed=allowed,
        )
    return _deliver(
        task_id, name, folder, prompt, session, event="unit_reworked", extra={"round": round_no},
        opener=opener, runner=runner, lister=lister, measure=measure, wait_s=wait_s,
        allowed=allowed,
    )


def _deliver(
    task_id: str, unit: str, folder: Path, prompt: str, session: str, *,
    event: str, extra: dict, opener, runner, lister, measure, wait_s: float,
    allowed: tuple[str, ...] = (),
) -> dict:
    """Send into the task's session; a session the harness no longer has becomes the one new tab."""
    try:
        return _enter_the_session(
            task_id, unit, session, folder, prompt, runner or _run_in_session,
            measure or costs.measure, event=event, extra=extra, allowed=allowed,
        )
    except SessionLost as lost:
        state.append_event(
            task_id, state.LOST_EVENT, unit=unit, harness="opencode",
            session=lost.session, reason=lost.reason,
        )
        return _open_first_tab(
            task_id, unit, folder, prompt, opener or windows.open_window, lister, wait_s,
            event=event, extra=extra, measure=measure, allowed=allowed,
        )



def corrections_text(task_id: str, unit: str, review: Path, round_no: int) -> str:
    """A pointer to the review round, never its text: the file is the one place it is read from."""
    brief = review.parent / f"20-brief-{unit}.md"
    return (
        f"Sei l'esecutore dell'unità {unit} del compito {task_id}, giro di correzione {round_no}. "
        f"Leggi le correzioni in {review} e il brief in {brief}, applica solo quelle. "
        "Lavora in questa copia, committa sul ramo locale, aggiorna il resoconto. Niente push."
    )


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
    list_sessions = lister or (lambda: _list_sessions(folder))
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


def _enter_the_session(
    task_id: str, unit: str, session: str, folder: Path, prompt: str, runner, measure,
    *, event: str, extra: dict, allowed: tuple[str, ...] = (),
) -> dict:
    """Send work into the task's own session, or queue it when that session is busy.

    A session the harness cannot report is not a busy session: it is gone, and
    queueing on it would leave the unit waiting on nothing for ever. A session
    on a model outside the owner's list is refused before anything is sent.
    """
    facts = _measure_or_lost(measure, session)
    model = str(getattr(facts, "model", "") or "")
    if allowed and not policy.model_allowed(model, allowed):
        reason = policy.refusal(model, allowed)
        state.append_event(
            task_id, "unit_model_refused", unit=unit, harness="opencode", session=session,
            model=model, allowed=list(allowed), reason=reason,
        )
        return {
            "unit": unit, "mode": "refused", "sent": False, "session": session,
            "model": model, "reason": reason,
        }
    if not facts.stopped:
        reason = "sessione occupata"
        state.append_event(
            task_id, "unit_queued", unit=unit, harness="opencode", session=session,
            reason=reason, **extra,
        )
        return {"unit": unit, "mode": "queued", "session": session, "reason": reason}
    proc = runner(session, prompt, folder)
    state.append_event(
        task_id, event, unit=unit, harness="opencode", session=session, mode="run",
        pid=getattr(proc, "pid", None), **extra,
    )
    row = costs.record(task_id, unit, "esecutore", session, window=windows.WINDOW_NAME)
    return {"unit": unit, "mode": "run", "session": session, "pid": getattr(proc, "pid", None), "row": row}


def _measure_or_lost(measure, session: str) -> costs.SessionFacts:
    """One session's facts, read twice at most: only "the export says it does not exist" means lost.

    A timeout, a locked store or a slow harness says nothing about the session:
    opening a new tab there left two sessions running on the same work. The
    second attempt costs one export; after it the engine says it cannot verify
    the session and opens nothing.
    """
    problem: costs.CostError | None = None
    for _attempt in range(2):
        try:
            return measure(session)
        except costs.SessionMissing as exc:
            raise SessionLost(session, str(exc)) from exc
        except costs.CostError as exc:
            problem = exc
    raise HarnessError(f"sessione {session} non verificabile, riprova: {problem}") from problem


def _open_first_tab(
    task_id: str, unit: str, folder: Path, prompt: str, opener, lister, wait_s: float,
    *, event: str = "unit_started", extra: dict | None = None,
    measure=None, allowed: tuple[str, ...] = (),
) -> dict:
    """No session for this harness yet: open the tab and bind the session it creates.

    The model of a session the tab has just opened is read back before the
    session is attached to the task: with a list configured, a tab that came up
    on a model outside it is named and left unattached, so the next `sup run`
    does not send work into it.
    """
    started_ms = int(time.time() * 1000)
    handle = opener(f"esecutore · {unit}", [_opencode_executable(), str(folder), "--prompt", prompt], cwd=folder)
    state.append_event(
        task_id, event, unit=unit, harness="opencode", window=str(handle.get("title", "")),
        mode="tab", **dict(extra or {}),
    )
    session = find_session(folder, started_ms, wait_s=wait_s, lister=lister)
    if session is None:
        return {"unit": unit, "mode": "tab", "window": handle, "session": None}
    model = _model_of(session, measure, allowed)
    if allowed and not policy.model_allowed(model, allowed):
        reason = policy.refusal(model, allowed)
        state.append_event(
            task_id, "unit_model_refused", unit=unit, harness="opencode",
            session=str(session.get("id") or ""), model=model, allowed=list(allowed), reason=reason,
        )
        return {
            "unit": unit, "mode": "refused", "sent": True, "window": handle,
            "session": session, "model": model, "reason": reason,
        }
    _attach(task_id, unit, session, handle)
    return {"unit": unit, "mode": "tab", "window": handle, "session": session}


def _model_of(session: dict, measure, allowed: tuple[str, ...]) -> str:
    """The model this session runs on; nothing is read when no list exists.

    A model that cannot be read stays empty, which a list refuses: a session the
    engine cannot check is not a session it may send work to.
    """
    if not allowed:
        return ""
    try:
        facts = (measure or costs.measure)(str(session.get("id") or ""))
    except costs.CostError:  # nexgen: allow reason="an export that just failed is reported as an unknown model, which a list refuses"
        return ""
    return str(getattr(facts, "model", "") or "")


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


def _unit_copy(task_id: str, unit: str) -> tuple[dict, Path]:
    """The unit's record and the folder its executor works in, refused when it is gone."""
    record = state.unit_record(task_id, unit)
    folder = Path(record.get("worktree") or "")
    if not folder.is_dir():
        raise HarnessError(f"copia dell'unità sparita: {folder}")
    return record, folder


def _attach(task_id: str, unit: str, session: dict, handle: dict) -> None:
    """Write the cost row and the journal event for the session the tab opened."""
    session_id = str(session.get("id") or "")
    row = costs.record(task_id, unit, "esecutore", session_id, window=str(handle.get("title", "")))
    state.append_event(
        task_id, "attached",
        unit=unit, role="esecutore", harness="opencode", session=row["session"],
    )


def _list_sessions(folder: str | Path) -> list[dict]:
    """Every session the harness knows for this folder's project, newest first.

    The harness lists the sessions of the project it is run in, so the question
    is asked from the copy: a unit whose repository is not the one the engine
    was started in still reports its own new session.
    """
    executable = _opencode_executable()
    argv = windows_command_argv([executable, "session", "list", "--format", "json", "-n", "100"])
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=LIST_TIMEOUT_S, check=False, cwd=str(folder),
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
