"""What an idle turn means: read its signs of life, retry it once, then call it stalled.

One job: turn "nothing moved for N minutes" into the consequence a person would
choose anyway -- the same turn started once more, and, if that is silent too, the
unit marked in the journal and the owner told through the megaphone. The watching
and the killing belong to `supervision_reaper`, which owns the process tree; this
module owns only the meaning of the stop, so the guardian stays a loop and the
policy behind it can be read on its own.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import psutil

from . import supervision_reaper as reaper
from . import supervision_state as state

#: The reason a turn was stopped because nothing moved; the guardian names it too.
IDLE_REASON = reaper.IDLE_REASON
#: How many times an idle turn is started again before its unit is called stalled.
MAX_IDLE_RETRIES = 1
#: How many files a progress scan reads before it accepts the partial picture.
FOLDER_SCAN_LIMIT = 4000


def signs_of_life(pid: int, folder: Path | str = "", logs: list[Path | str] | None = None) -> tuple:
    """The turn's signs of life: its tree's CPU time, the files it touched, its output.

    The CPU time catches a turn that is doing work without writing anything yet; the
    folder scan catches files written by a child the tree walk no longer sees; the
    logs catch a harness that prints its progress. The scan is bounded: a folder
    larger than the limit still reports its first files and their newest change.
    An empty folder leaves the scan out and keeps the CPU time, which every turn has.
    """
    return (
        _tree_cpu(pid),
        _folder_signature(Path(folder)) if folder else None,
        tuple(_file_size(path) for path in logs or ()),
    )


def _tree_cpu(pid: int) -> float:
    """User plus system seconds spent by `pid` and every descendant, rounded to a hundredth."""
    try:
        root = psutil.Process(pid)
        total = _cpu_of(root)
        for child in root.children(recursive=True):
            total += _cpu_of(child)
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="un albero che esce mentre lo leggiamo non ha segni di vita da riportare"
        return 0.0
    return round(total, 2)


def _cpu_of(proc) -> float:
    """The CPU seconds of one process, zero when it cannot be read."""
    try:
        times = proc.cpu_times()
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="un processo che esce o nega le sue statistiche non aggiunge CPU"
        return 0.0
    return float(times.user) + float(times.system)


def _folder_signature(folder: Path) -> tuple[int, int, int]:
    """How many entries the folder shows, its newest modification, and their total size.

    A bounded scan rather than a full tree read: a copy with a huge dependency
    folder must not make the guardian slower than the turn.
    """
    count = 0
    newest = 0
    total = 0
    stack = [folder]
    while stack and count < FOLDER_SCAN_LIMIT:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:  # nexgen: allow reason="una cartella che sparisce durante la scansione non ha file da contare"
            continue
        for entry in entries:
            if count >= FOLDER_SCAN_LIMIT:
                break
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:  # nexgen: allow reason="una voce che sparisce durante la scansione non ha una firma"
                continue
            count += 1
            total += int(stat.st_size)
            newest = max(newest, int(stat.st_mtime_ns))
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:  # nexgen: allow reason="una voce che sparisce durante la scansione non è una cartella da aprire"
                is_dir = False
            if is_dir:
                stack.append(Path(entry.path))
    return count, newest, total


def _file_size(path: Path | str) -> int:
    """The current size of one log, zero when it does not exist yet."""
    try:
        return int(Path(path).stat().st_size)
    except OSError:  # nexgen: allow reason="un log non ancora creato ha dimensione zero"
        return 0


def probe_for(pid: int, launch: dict | None):
    """The signs-of-life reader for this turn: everything the recipe names, CPU alone otherwise."""
    folder = str((launch or {}).get("folder") or "")
    logs = [str(path) for path in (launch or {}).get("logs") or []]
    return lambda: signs_of_life(pid, folder, logs)


def recipe_of(raw: str) -> dict | None:
    """The relaunch recipe as the supervisor wrote it, or None when it does not parse."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:  # nexgen: allow reason="una ricetta illeggibile si comporta come nessuna ricetta: lo stallo viene detto lo stesso"
        return None
    return parsed if isinstance(parsed, dict) else None


def guard(
    task_id: str, unit: str, pid: int, created: float, *,
    timeout_s: float = reaper.DEFAULT_TIMEOUT_S, job: str = "", idle_s: float = 0.0,
    poll_s: float = reaper.POLL_S, grace_s: float = reaper.GRACE_S, launch: dict | None = None,
    attempt: int = 1, progress=None, reap_fn=None, relaunch_fn=None, hold_fn=None,
    alert_fn=None,
) -> dict:
    """Watch one turn and carry it through an idle stop: one retry, then the stall.

    The retry is the point of the whole state machine: a turn that hangs before it
    reads anything is usually a cold start that the same command can survive, and a
    person should not have to relaunch it by hand. The second idle stop is a real
    stall: the unit is marked in the journal and the owner is told once.
    """
    result = (reap_fn or reaper.reap)(
        pid, created, timeout_s=timeout_s, job=job, poll_s=poll_s, grace_s=grace_s,
        idle_s=idle_s, progress=progress,
    )
    _journal_reaped(task_id, unit, pid, result)
    if result.get("reason") != IDLE_REASON:
        return result

    minutes = round(float(result.get("idle_s") or idle_s or 0.0) / 60.0, 1)
    _journal(task_id, "turn_idle", unit=unit, pid=pid, minutes=minutes, attempt=attempt)
    if attempt <= MAX_IDLE_RETRIES and launch:
        relaunched = _retry(task_id, unit, pid, launch, attempt, relaunch_fn=relaunch_fn, hold_fn=hold_fn)
        if relaunched:
            return result
    _stall(task_id, unit, pid, minutes, idle_s, alert_fn=alert_fn)
    return result


def _retry(task_id, unit, pid, launch, attempt, *, relaunch_fn=None, hold_fn=None) -> bool:
    """Start the same turn again and hand it to its own guardian; False when it cannot start."""
    try:
        proc = (relaunch_fn or _relaunch)(task_id, unit, launch)
    except Exception as exc:  # nexgen: allow reason="un rilancio che non parte diventa lo stallo che l'unità merita"
        _journal(
            task_id, "turn_idle_relaunch", unit=unit, pid=pid, attempt=attempt,
            outcome="non riuscito", reason=" ".join(str(exc).splitlines())[:300],
        )
        return False
    _journal(
        task_id, "turn_idle_relaunch", unit=unit, pid=pid, attempt=attempt,
        outcome="rilanciato", new_pid=getattr(proc, "pid", None),
    )
    try:
        (hold_fn or _hold_relaunched)(task_id, unit, proc)
    except Exception as exc:  # nexgen: allow reason="un turno già rilanciato che non si sorveglia non va dichiarato in stallo"
        _journal(
            task_id, "turn_idle_relaunch", unit=unit, pid=pid, attempt=attempt,
            outcome="non sorvegliato", reason=" ".join(str(exc).splitlines())[:300],
        )
    return True


def _relaunch(task_id, unit, launch):
    """Start the same turn once more; imported here so the guardian module owns no harness."""

    from .supervision_turn_launch import relaunch_turn

    return relaunch_turn(task_id, unit, launch)


def _hold_relaunched(task_id, unit, proc) -> None:
    """Give the relaunched turn its own guardian, marked as the second attempt."""

    from .supervision_turn_launch import hold_turn

    hold_turn(task_id, unit, proc, attempt=MAX_IDLE_RETRIES + 1)


def _stall(task_id: str, unit: str, pid: int, minutes: float, idle_s: float, *, alert_fn=None) -> None:
    """Mark the unit stalled in the journal and tell the owner once."""
    session = ""
    try:
        session = str(state.unit_record(task_id, unit).get("session") or "")
    except state.SupervisionError:  # nexgen: allow reason="un'unità il cui diario non si legge resta senza sessione nel fatto di stallo"
        pass
    _journal(
        task_id, "unit_stalled", unit=unit, session=session, pid=pid,
        minutes=minutes, threshold=round(idle_s / 60.0, 1), reason="inattivita",
    )
    (alert_fn or _alert)(unit, session, minutes)


def _alert(unit: str, session: str, minutes: float) -> None:
    """The owner's alert, once per stalled unit and session, through the megaphone.

    The measured minutes travel with the sentence: without them the person reads
    the `{minutes}` placeholder instead of how long the task stayed silent.
    """
    try:
        from nexgen_core import alerts
        from nexgen_core.megaphone import Megaphone

        title, message, action = alerts.TASK_STALLED.say(minutes=f"{minutes:g}")
        Megaphone().send_alert(
            title=title, message=message, action=action,
            alert_key=f"turn_idle:{session or unit}",
        )
    except Exception:  # nexgen: allow reason="un avviso perso non annulla lo stallo già scritto nel diario"
        pass


def _journal_reaped(task_id: str, unit: str, pid: int, result: dict) -> None:
    """One `turn_reaped` row naming what the guardian stopped and why."""
    _journal(
        task_id, "turn_reaped", unit=unit, pid=pid, reason=result.get("reason"),
        killed=result.get("killed"), spared=result.get("spared"), refused=result.get("refused", []),
    )


def _journal(task_id: str, event: str, **fields) -> None:
    """Write one journal row, and never fail the guardian over a journal it cannot open."""
    try:
        state.append_event(task_id, event, **fields)
    except (OSError, state.SupervisionError):  # nexgen: allow reason="un diario non scrivibile o assente non deve far fallire la pulizia"
        pass
