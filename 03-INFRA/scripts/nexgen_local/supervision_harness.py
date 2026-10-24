# nexgen: one-job reason="drive one unit's work into its harness session and say what the harness cannot do"
"""The executor's harness: one session per task, its window, its prompt, its cost.

One harness per task, and every harness is an adapter: the engine asks the
registry for the harness a role names, builds the tab, the turn and the resume
through that adapter, and says plainly which operation the harness does not have.
A session belongs to the copy it was born in: it is reused only when the folder
the journal recorded is this unit's copy, otherwise the engine opens the one new
tab in the right copy and says why. The engine never pastes the brief into a
command line, never picks a model, and never reads what the terminal prints.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from nexgen_core.processes import no_window_kwargs, windows_command_argv

from . import supervision_adapters as adapters
from . import supervision_costs as costs
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
#: The journal events that say whether the tab the engine opened is still there.
TAB_EVENTS = ("tab_closed", "tab_left_open")
#: The harnesses this engine drives through OpenCode's own executable resolution.
_OPENCODE_LIKE = ("opencode", "local")


class HarnessError(SupervisionError):
    """The harness is missing, its session list is unreadable, or the unit has no copy to run in."""


class SessionLost(HarnessError):
    """The task's session is gone from the harness: deleted, pruned, or unreadable."""

    def __init__(self, session: str, reason: str) -> None:
        super().__init__(f"la sessione {session} non esiste più: {reason}")
        self.session = session
        self.reason = reason


def run_unit(
    task_id: str, unit: str, *, harness: str = "", opener=None, runner=None, lister=None,
    measure=None, wait_s: float = SESSION_WAIT_S,
) -> dict:
    """Put the unit's work into its harness session, queue it, or open the first tab.

    The harness is the one named for the executor role, unless the owner named one
    for this run. A unit whose recorded session was born in another folder gets a
    new tab in the unit's own copy: the harness binds a session to its directory,
    and reusing it makes the executor work in a folder that may no longer exist.
    """
    record, folder = _unit_copy(task_id, unit)
    name = record["unit"]
    chosen = _resolve(task_id, "esecutore", harness)
    adapter = _adapter(chosen)
    brief = state.open_task(task_id) / f"20-brief-{name}.md"
    if not brief.is_file():
        raise HarnessError(f"brief dell'unità sparito: {brief}")
    local_notes = _prepare_local(task_id, name, chosen)
    if adapter.capabilities.has("write_permissions"):
        adapter.write_profile(task_id, folder)
    allowed = policy.allowed_models()
    prompt = prompt_text(task_id, name, brief, harness=chosen)
    previous = state.harness_session_record(task_id, chosen)
    if not previous:
        opened = _open_first_tab(
            task_id, name, folder, prompt, opener or windows.open_window, lister, wait_s,
            adapter=adapter, measure=measure, allowed=allowed, runner=runner,
        )
        opened["harness"] = chosen
        opened["notes"] = local_notes
        return opened
    if _foreign_copy(previous.get("folder") or "", folder):
        state.append_event(
            task_id, "harness_session_foreign", unit=name, harness=chosen,
            session=previous["session"], session_folder=previous.get("folder") or "",
            copy=str(folder), reason="la sessione è nata in un'altra copia",
        )
        opened = _open_first_tab(
            task_id, name, folder, prompt, opener or windows.open_window, lister, wait_s,
            adapter=adapter, measure=measure, allowed=allowed, runner=runner,
            reason="sessione nata in un'altra copia: scheda nuova nella copia dell'unità",
        )
        opened["harness"] = chosen
        opened["notes"] = local_notes
        return opened
    result = _deliver(
        task_id, name, folder, prompt, previous["session"], event="unit_started", extra={},
        opener=opener, runner=runner, lister=lister, measure=measure, wait_s=wait_s,
        allowed=allowed, adapter=adapter,
    )
    result["harness"] = chosen
    result["notes"] = local_notes
    return result


def send_corrections(
    task_id: str, unit: str, review: str | Path, round_no: int, *,
    runner=None, measure=None, opener=None, lister=None, wait_s: float = SESSION_WAIT_S,
    open_without_session: bool = False, model: str = "",
) -> dict:
    """Put a review round's corrections into the unit's session, or queue them while it is busy.

    The corrections stay in their file: the command line carries a pointer, the
    same way `sup run` points at the brief. The harness is the one that executed
    the unit, so a task with two harnesses sends each unit's corrections to the
    session that did its work. Without a session there is nothing to correct, and
    opening a window on a unit nobody ran would be a lie; a session that
    disappeared is a different thing, and gets the one new tab.
    """
    record, folder = _unit_copy(task_id, unit)
    name = record["unit"]
    chosen = str(record.get("harness") or _resolve(task_id, "esecutore", ""))
    adapter = _adapter(chosen)
    path = Path(review)
    if not path.is_file():
        raise HarnessError(f"file di revisione sparito: {path}")
    if adapter.capabilities.has("write_permissions"):
        adapter.write_profile(task_id, folder)
    prompt = corrections_text(task_id, name, path, round_no)
    allowed = policy.allowed_models()
    previous = state.harness_session_record(task_id, chosen)
    if not previous:
        if not open_without_session:
            raise HarnessError(
                f"nessuna sessione harness sul compito: la correzione dell'unità {name} non ha "
                "dove arrivare (`sup run` apre la prima sessione)"
            )
        opened = _open_first_tab(
            task_id, name, folder, prompt, opener or windows.open_window, lister, wait_s,
            event="unit_reworked", extra={"round": round_no}, adapter=adapter,
            measure=measure, allowed=allowed, runner=runner,
        )
    elif _foreign_copy(previous.get("folder") or "", folder):
        state.append_event(
            task_id, "harness_session_foreign", unit=name, harness=chosen,
            session=previous["session"], session_folder=previous.get("folder") or "",
            copy=str(folder), reason="la sessione è nata in un'altra copia",
        )
        opened = _open_first_tab(
            task_id, name, folder, prompt, opener or windows.open_window, lister, wait_s,
            event="unit_reworked", extra={"round": round_no}, adapter=adapter,
            measure=measure, allowed=allowed, runner=runner,
            reason="sessione nata in un'altra copia: scheda nuova nella copia dell'unità",
        )
    else:
        opened = _deliver(
            task_id, name, folder, prompt, previous["session"], event="unit_reworked",
            extra={"round": round_no}, opener=opener, runner=runner, lister=lister,
            measure=measure, wait_s=wait_s, allowed=allowed, model=model, adapter=adapter,
        )
    opened["harness"] = chosen
    if model and opened.get("mode") != "run":
        opened["model_requested"] = model
        opened["model_applied"] = False
    return opened


def _deliver(
    task_id: str, unit: str, folder: Path, prompt: str, session: str, *,
    event: str, extra: dict, opener, runner, lister, measure, wait_s: float,
    allowed: tuple[str, ...] = (), model: str = "", adapter=None,
) -> dict:
    """Send into the task's session; a session the harness no longer has becomes the one new tab."""
    adapter = adapter or _adapter("opencode")
    try:
        return _enter_the_session(
            task_id, unit, session, folder, prompt, runner or _runner_for(adapter),
            measure or _measure_for(adapter), event=event, extra=extra, allowed=allowed,
            model=model, opener=opener, adapter=adapter,
        )
    except SessionLost as lost:
        state.append_event(
            task_id, state.LOST_EVENT, unit=unit, harness=adapter.name,
            session=lost.session, reason=lost.reason,
        )
        opened = _open_first_tab(
            task_id, unit, folder, prompt, opener or windows.open_window, lister, wait_s,
            event=event, extra=extra, adapter=adapter, measure=measure, allowed=allowed, runner=runner,
        )
        if model:
            opened["model_requested"] = model
            opened["model_applied"] = False
        return opened


def corrections_text(task_id: str, unit: str, review: Path, round_no: int) -> str:
    """A pointer to the review round, never its text: the file is the one place it is read from."""
    brief = review.parent / f"20-brief-{unit}.md"
    return (
        f"Sei l'esecutore dell'unità {unit} del compito {task_id}, giro di correzione {round_no}. "
        f"Leggi le correzioni in {review} e il brief in {brief}, applica solo quelle. "
        "Lavora in questa copia, committa sul ramo locale, aggiorna il resoconto. Niente push."
    )


def prompt_text(task_id: str, unit: str, brief: Path, *, harness: str = "opencode") -> str:
    """A pointer to the brief, never the brief: the file stays the one place it is read from.

    A local unit gets two extra sentences: its brief is a step-by-step plan because
    the local model executes instead of designing, and the acceptance tests are
    already written red by somebody else, so touching them is not its work.
    """
    report = brief.parent / f"30-resoconto-{unit}.md"
    text = (
        f"Sei l'esecutore dell'unità {unit} del compito {task_id}. "
        f"Leggi il brief in {brief} e segui solo quello. "
        f"Lavora in questa copia, committa sul ramo locale, scrivi il resoconto in {report}. "
        "Niente push."
    )
    if harness == "local":
        text += (
            " Il brief è un piano passo per passo: esegui i passi nell'ordine, non riprogettare. "
            "I test di accettazione sono già scritti e rossi: non modificarli, fai passare quelli."
        )
    return text


def find_session(
    folder: Path, since_ms: int, *, wait_s: float = SESSION_WAIT_S, lister=None,
    poll_s: float = POLL_S, clock=None, sleep=None,
) -> dict | None:
    """The newest OpenCode session opened in `folder` at or after `since_ms`, or None.

    Kept as a module function because it is the engine's oldest public step: the
    adapter does the reading, this wrapper returns the shape the journal and the
    existing callers already read.
    """
    adapter = _adapter("opencode")
    try:
        found = adapter.find_session(
            folder,
            since_ms,
            executable=_opencode_executable() if lister is None else "",
            wait_s=wait_s,
            lister=lister or (lambda: _list_sessions(folder)),
            poll_s=poll_s,
            clock=clock,
            sleep=sleep,
        )
    except adapters.base.AdapterError as exc:
        raise HarnessError(str(exc)) from exc
    return found.as_dict() if found else None


def _enter_the_session(
    task_id: str, unit: str, session: str, folder: Path, prompt: str, runner, measure,
    *, event: str, extra: dict, allowed: tuple[str, ...] = (), model: str = "", opener=None,
    adapter=None,
) -> dict:
    """Send work into the task's own session, or queue it when that session is busy.

    A session the harness cannot report is not a busy session: it is gone, and
    queueing on it would leave the unit waiting on nothing for ever. A session
    on a model outside the owner's list is refused before anything is sent. When
    the engine closed this task's tab, the work goes into a tab again: a worker
    the owner cannot see is what this engine exists to prevent.
    """
    adapter = adapter or _adapter("opencode")
    adapter.require("send_turn", "una sessione esistente non può ricevere un turno")
    facts = _measure_or_lost(measure, session)
    running = str(getattr(facts, "model", "") or "")
    chosen = model or running
    if allowed and not policy.model_allowed(chosen, allowed):
        reason = policy.refusal(chosen, allowed)
        state.append_event(
            task_id, "unit_model_refused", unit=unit, harness=adapter.name, session=session,
            model=chosen, allowed=list(allowed), reason=reason, folder=str(folder),
        )
        return {
            "unit": unit, "mode": "refused", "sent": False, "session": session,
            "model": chosen, "reason": reason,
        }
    if not facts.stopped:
        reason = "sessione occupata"
        state.append_event(
            task_id, "unit_queued", unit=unit, harness=adapter.name, session=session,
            reason=reason, folder=str(folder), **extra,
        )
        return {"unit": unit, "mode": "queued", "session": session, "reason": reason}
    if _tab_closed(task_id):
        return _reopen_tab(
            task_id, unit, session, folder, prompt, event=event, extra=extra,
            opener=opener, model=model, adapter=adapter,
        )
    proc = runner(session, prompt, folder, model)
    state.append_event(
        task_id, event, unit=unit, harness=adapter.name, session=session, mode="run",
        pid=getattr(proc, "pid", None), folder=str(folder), **extra,
    )
    row = costs.record(
        task_id, unit, "esecutore", session, harness=adapter.name,
        window=windows.WINDOW_NAME,
    )
    return {
        "unit": unit, "mode": "run", "session": session, "pid": getattr(proc, "pid", None),
        "row": row, "model": model, "model_applied": bool(model),
    }


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


def _opening(adapter, task_id: str, unit: str) -> tuple:
    """The adapter, its CLI and the id it will use: resolved and refused before anything opens.

    A harness that is not installed stops here, before a window or a turn, and a
    harness that chooses its own session id does it here so the attach has
    something to look for.
    """
    chosen = adapter or _adapter("opencode")
    executable = _executable_for(chosen)
    identifier = chosen.new_session_id(task_id, unit) if hasattr(chosen, "new_session_id") else ""
    return chosen, executable, identifier


def _open_first_tab(
    task_id: str, unit: str, folder: Path, prompt: str, opener, lister, wait_s: float,
    *, event: str = "unit_started", extra: dict | None = None, adapter=None,
    measure=None, allowed: tuple[str, ...] = (), reason: str = "", runner=None,
) -> dict:
    """No session for this harness yet: open the tab and bind the session it creates.

    A harness with its own visible sessions gets a turn instead of a tab: the
    engine starts the work and attaches the key it chose, because a window of the
    engine on top of a session OpenClaw already shows would be the copy this
    engine exists to avoid. The model of a session the tab has just opened is read
    back before the session is attached to the task: with a list configured, a tab
    that came up on a model outside it is named and left unattached, so the next
    `sup run` does not send work into it. A harness that cannot say which session
    a tab opened leaves the attach manual and says so; the id is never invented.
    """
    adapter, executable, chosen = _opening(adapter, task_id, unit)
    if not adapter.capabilities.window:
        return _open_turn(
            task_id, unit, folder, prompt, session=chosen, event=event, extra=extra or {},
            runner=runner or _runner_for(adapter), adapter=adapter, measure=measure,
            allowed=allowed, lister=lister, wait_s=wait_s, reason=reason,
        )
    started_ms = int(time.time() * 1000)
    argv = adapter.tab_argv(executable, folder, prompt, session=chosen)
    handle = opener(
        f"esecutore · {unit}", argv, cwd=folder,
        marker=windows.tab_marker(task_id, "esecutore"),
    )
    state.append_event(
        task_id, event, unit=unit, harness=adapter.name, window=str(handle.get("title", "")),
        mode="tab", folder=str(folder), **dict(extra or {}),
    )
    found, note = _session_after(adapter, folder, started_ms, lister, wait_s, chosen)
    if found is None:
        result = {
            "unit": unit, "mode": "tab", "window": handle, "session": None,
            "harness": adapter.name,
        }
        result["reason"] = reason or note
        return result
    session = found
    model = _model_of(session, measure or _measure_for(adapter), allowed)
    if allowed and not policy.model_allowed(model, allowed):
        refusal = policy.refusal(model, allowed)
        state.append_event(
            task_id, "unit_model_refused", unit=unit, harness=adapter.name,
            session=str(session.get("id") or ""), model=model, allowed=list(allowed),
            reason=refusal, folder=str(folder),
        )
        return {
            "unit": unit, "mode": "refused", "sent": True, "window": handle,
            "session": session, "model": model, "reason": refusal,
        }
    _attach(task_id, unit, session, handle, adapter.name, folder)
    result = {"unit": unit, "mode": "tab", "window": handle, "session": session}
    if reason:
        result["reason"] = reason
    return result


def _open_turn(
    task_id: str, unit: str, folder: Path, prompt: str, *, session: str, event: str,
    extra: dict, runner, adapter, measure, allowed: tuple[str, ...], lister, wait_s: float,
    reason: str = "",
) -> dict:
    """Start the first turn of a harness that has no window, and attach the key it used.

    The turn is the delivery: the same one-shot runner that carries a correction
    carries the brief's pointer, and the session is attached under the key the
    engine chose, so the attach is not a guess made from a clock. A session the
    harness has not listed yet is reported as such, and the journal still says who
    the work was given to.
    """
    started_ms = int(time.time() * 1000)
    proc = runner(session, prompt, folder, "")
    pid = getattr(proc, "pid", None)
    state.append_event(
        task_id, event, unit=unit, harness=adapter.name, session=session, mode="turn",
        pid=pid, folder=str(folder), **dict(extra or {}),
    )
    found, note = _session_after(adapter, folder, started_ms, lister, wait_s, session)
    if found is None:
        result = {
            "unit": unit, "mode": "run", "session": session, "pid": pid, "row": None,
            "reason": reason or note,
        }
        return result
    model = _model_of(found, measure or _measure_for(adapter), allowed)
    if allowed and not policy.model_allowed(model, allowed):
        refusal = policy.refusal(model, allowed)
        state.append_event(
            task_id, "unit_model_refused", unit=unit, harness=adapter.name,
            session=str(found.get("id") or session), model=model, allowed=list(allowed),
            reason=refusal, folder=str(folder), sent=True,
        )
        return {
            "unit": unit, "mode": "refused", "sent": True, "session": found,
            "model": model, "reason": refusal,
        }
    row = _attach(task_id, unit, found, {"title": ""}, adapter.name, folder)
    result = {
        "unit": unit, "mode": "run", "session": str(found.get("id") or session),
        "pid": pid, "row": row, "model": model,
    }
    if reason:
        result["reason"] = reason
    return result


def _session_after(adapter, folder: Path, started_ms: int, lister, wait_s: float, chosen: str):
    """The session a just-opened tab created, and the note when it cannot be told.

    OpenCode keeps the module-level `find_session` as its seam, exactly as before
    the adapters existed; the other harnesses are asked through their own store.
    """
    if not adapter.capabilities.has("find_session"):
        return None, (
            f"l'harness {adapter.name} non sa elencare le sessioni: la scheda è aperta, "
            "la sessione resta da agganciare a mano"
        )
    if adapter.name in _OPENCODE_LIKE:
        return find_session(folder, started_ms, wait_s=wait_s, lister=lister), ""
    try:
        found = adapter.find_session(
            folder, started_ms, executable=_executable_for(adapter), wait_s=wait_s,
            expected=chosen,
        )
    except adapters.base.AdapterError as exc:
        return None, f"sessioni di {adapter.name} non leggibili ({exc}): aggancio a mano"
    if found is None:
        return None, f"nessuna sessione di {adapter.name} è comparsa: aggancio a mano"
    return found.as_dict(), ""


def _model_of(session: dict, measure, allowed: tuple[str, ...]) -> str:
    """The model this session runs on; nothing is read when no list exists.

    A model that cannot be read stays empty, which a list refuses: a session the
    engine cannot check is not a session it may send work to.
    """
    if not allowed:
        return ""
    try:
        facts = measure(str(session.get("id") or ""))
    except costs.CostError:  # nexgen: allow reason="an export that just failed is reported as an unknown model, which a list refuses"
        return ""
    return str(getattr(facts, "model", "") or "")


def _reopen_tab(
    task_id: str, unit: str, session: str, folder: Path, prompt: str, *,
    event: str, extra: dict, opener, model: str = "", adapter=None,
) -> dict:
    """The tab the engine closed is opened again on the session the task already has.

    The adapter builds the command: for OpenCode it is `<folder> -s <session>
    --prompt "<pointer>"`, the TUI on an existing session (verified against
    OpenCode 2's own `--session` flag), so the owner watches the corrections
    arrive instead of a hidden `run`.
    """
    adapter = adapter or _adapter("opencode")
    adapter.require("resume", "una scheda chiusa non può essere riaperta")
    handle = (opener or windows.open_window)(
        f"esecutore · {unit}",
        adapter.resume_argv(_executable_for(adapter), session, folder, prompt),
        cwd=folder, marker=windows.tab_marker(task_id, "esecutore"),
    )
    state.append_event(
        task_id, event, unit=unit, harness=adapter.name, window=str(handle.get("title", "")),
        mode="tab", session=session, reopened=True, folder=str(folder), **extra,
    )
    row = costs.record(
        task_id, unit, "esecutore", session, harness=adapter.name, window=windows.WINDOW_NAME,
    )
    result = {
        "unit": unit, "mode": "tab", "session": {"id": session}, "window": handle,
        "row": row, "reopened": True,
    }
    if model:
        result["model_requested"] = model
        result["model_applied"] = False
    return result


def _tab_closed(task_id: str, role: str = "esecutore") -> bool:
    """Whether the engine's own last word on this tab is that it closed it.

    The journal records what the engine did: after `tab_closed` the next
    delivery has no window to run in, and a review round must open one. A tab
    the owner closed by hand is not in this journal, and there the engine still
    delivers with a one-shot turn: it has no way to know.
    """
    last = ""
    for row in state.read_events(task_id):
        if row.get("event") in TAB_EVENTS and str(row.get("role") or "") == role:
            last = str(row["event"])
    return last == "tab_closed"


def _run_in_session(session: str, prompt: str, folder: Path, model: str = ""):
    """One-shot work inside the task's OpenCode session; the terminal shows it, the engine does not watch.

    `-m` is the owner's escape from a provider that refuses the configured model;
    `run` has no `--variant`, so the model alone is what travels.
    """
    return _run_turn(_adapter("opencode"), _opencode_executable(), session, prompt, folder, model)


def _run_turn(adapter, executable: str, session: str, prompt: str, folder: Path, model: str = ""):
    """One turn through this harness, detached: no window of the engine is left holding it.

    The copy is checked first, because one harness runs its turn in a workspace
    the owner configured and a wrong folder would look like work in the unit. A
    harness whose numbers are only in what the turn prints writes them into its
    own log, so a turn that ends on its own leaves its cost behind.
    """
    adapter.check_folder(folder)
    argv = windows_command_argv(adapter.send_argv(executable, session, prompt, model=model))
    log = adapter.turn_log(session)
    try:
        if log is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8", newline="\n") as handle:
                return subprocess.Popen(
                    argv, cwd=folder,
                    stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.DEVNULL,
                    **_detach_kwargs(),
                )
        return subprocess.Popen(
            argv, cwd=folder,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **_detach_kwargs(),
        )
    except OSError as exc:
        raise HarnessError(f"sessione {session} non raggiungibile: {exc}") from exc


def _runner_for(adapter):
    """The one-shot turn of this harness, with its own executable resolved from PATH.

    OpenCode keeps the module-level `_run_in_session` as its default: it is the
    tested seam the callers replace, and its argv is built by the same adapter.
    """
    if adapter.name in _OPENCODE_LIKE:
        return _run_in_session
    return lambda session, prompt, folder, model="": _run_turn(
        adapter, _executable_for(adapter), session, prompt, folder, model,
    )


def _measure_for(adapter):
    """This harness's cost reader, named so an adapter that cannot measure still raises `CostError`."""
    return lambda session: costs.measure_for(adapter.name, session)


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


def _attach(task_id: str, unit: str, session: dict, handle: dict, harness: str, folder: Path) -> dict:
    """Write the cost row and the journal event for the session the tab opened."""
    session_id = str(session.get("id") or "")
    row = costs.record(
        task_id, unit, "esecutore", session_id, harness=harness,
        window=str(handle.get("title", "")),
    )
    state.append_event(
        task_id, "attached",
        unit=unit, role="esecutore", harness=harness, session=row["session"],
        folder=str(folder),
    )
    return row


def _list_sessions(folder: str | Path) -> list[dict]:
    """Every session the harness knows for this folder's project, newest first.

    The harness lists the sessions of the project it is run in, so the question
    is asked from the copy: a unit whose repository is not the one the engine
    was started in still reports its own new session. The adapter builds the
    command; the process is started through this module's `subprocess`, which is
    the seam the tests replace.
    """
    try:
        return _adapter("opencode").list_sessions(
            folder, executable=_opencode_executable(), runner=subprocess.run,
        )
    except adapters.base.AdapterError as exc:
        raise HarnessError(str(exc)) from exc


def _opencode_executable() -> str:
    return costs.opencode_executable()


def _executable_for(adapter) -> str:
    """The harness's own CLI, resolved where the caller expects it.

    OpenCode keeps its tested resolution (PATH, and the `.cmd` shim on Windows);
    every other harness is looked up by its declared executable name, and a
    missing one is refused before a tab is opened.
    """
    if adapter.name in _OPENCODE_LIKE:
        return _opencode_executable()
    found = shutil.which(adapter.executable)
    if not found:
        raise HarnessError(
            f"harness {adapter.name} non installato: `{adapter.executable}` non è nel PATH"
        )
    return found


def _adapter(name: str):
    """The adapter a journal name means, refused with the names that exist."""
    try:
        return adapters.get(name)
    except adapters.base.AdapterError as exc:
        raise HarnessError(str(exc)) from exc


def _resolve(task_id: str, role: str, chosen: str) -> str:
    """The harness this run uses: the one asked for, or the one the lane gives the role."""
    name = (chosen or "").strip()
    if name:
        return _adapter(name).name
    return policy.harness_for(role)


def _prepare_local(task_id: str, unit: str, chosen: str) -> list[str]:
    """Start the lane's local server when this unit runs on it, after the memory check."""
    if chosen != "local":
        return []
    from . import supervision_local_model as local

    return local.ensure_running(task_id, unit)


def _foreign_copy(recorded: str, folder: Path) -> bool:
    """Whether a recorded session folder is a different copy; a legacy empty folder is reused."""
    if not recorded:
        return False
    return adapters.base.normal_path(recorded) != adapters.base.normal_path(folder)


def _normal(path: str | Path) -> str:
    """A folder as one comparable string, case and separators aside."""
    return adapters.base.normal_path(path)
