"""Detached per-turn guardian that waits for a harness turn and cleans up its tree.

One job: watch one detached turn process until it exits, shows no sign of life for
the lane's idle threshold, or reaches its timeout, then terminate every descendant,
process-group member or Windows job member it left behind while sparing the shared
`opencode serve` and never touching a recycled pid. A turn stopped for idleness is
started once more; when the retry is idle too, the unit is marked stalled and the
owner is told through the megaphone.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from nexgen_core.processes import no_window_kwargs

from . import supervision_job as jobs
from . import supervision_processes as processes
from . import supervision_state as state

DEFAULT_TIMEOUT_S = 4 * 3600
POLL_S = 2.0
GRACE_S = 3.0
#: The reason a turn was stopped because nothing moved.
IDLE_REASON = "inattivo"
#: How often the signs of life are read; reading the tree every two seconds would
#: cost more than the turn it is watching.
IDLE_POLL_S = 30.0
#: How many times an idle turn is started again before its unit is called stalled.
MAX_IDLE_RETRIES = 1
#: How many files a progress scan reads before it accepts the partial picture.
FOLDER_SCAN_LIMIT = 4000
_CREATED_SLACK_S = 0.5
_REAL_POPEN = subprocess.Popen  # captured at import, before a test replaces it


def watchable(proc) -> bool:
    """Whether this is a process this engine really started, and so one a guardian may watch."""
    return isinstance(proc, _REAL_POPEN)


def watch(
    task_id: str, unit: str, proc, *, timeout_s: float = DEFAULT_TIMEOUT_S, job: str = "",
    idle_s: float = 0.0, launch: dict | None = None, attempt: int = 1,
) -> str:
    """Spawn a detached windowless guardian for `proc`, or return an empty string for test doubles.

    `launch` is the recipe the guardian uses to start the same turn again after an
    idle stop. It stays None for a turn started by something this engine cannot
    rebuild, and the guardian then reports the stall instead of relaunching.
    """
    if not watchable(proc):
        return ""
    pid = getattr(proc, "pid", None)
    if not pid:
        return "turno già finito"
    try:
        target = psutil.Process(int(pid))
        if not _is_alive(target):
            return "turno già finito"
        created = target.create_time()
    except psutil.NoSuchProcess:  # nexgen: allow reason="un processo già uscito prima del watch non ha più nulla da sorvegliare"
        return "turno già finito"
    except psutil.AccessDenied as exc:
        return " ".join(f"accesso negato al processo {pid}: {exc}".splitlines())

    scripts_dir = str(Path(__file__).resolve().parent.parent)
    argv = [
        sys.executable, "-m", "nexgen_local.supervision_reaper",
        "--task", task_id, "--unit", unit, "--pid", str(pid),
        "--created", repr(created), "--timeout", str(timeout_s), "--job", job,
        "--idle", str(idle_s), "--attempt", str(attempt),
    ]
    if launch:
        argv.extend(["--launch", json.dumps(launch, ensure_ascii=True)])
    try:
        _REAL_POPEN(
            argv, cwd=scripts_dir, env=_guardian_env(scripts_dir),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, **_guardian_detach_kwargs(),
        )
    except OSError as exc:
        return " ".join(f"impossibile avviare il guardiano: {exc}".splitlines())
    return ""


def _guardian_env(scripts_dir: str) -> dict[str, str]:
    """Inherited environment with `scripts_dir` on `PYTHONPATH` so `-m nexgen_local...` resolves."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parts = existing.split(os.pathsep) if existing else []
    if scripts_dir not in parts:
        env["PYTHONPATH"] = f"{scripts_dir}{os.pathsep}{existing}" if existing else scripts_dir
    return env


def _guardian_detach_kwargs() -> dict:
    """Windowless, detached process kwargs for the guardian on Windows and POSIX."""
    if os.name == "nt":
        kwargs = no_window_kwargs()
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | new_group
        return kwargs
    return {"start_new_session": True}


def reap(
    pid: int,
    created: float,
    *,
    timeout_s: float,
    job: str = "",
    poll_s: float = POLL_S,
    grace_s: float = GRACE_S,
    clock=time.monotonic,
    sleep=time.sleep,
    idle_s: float = 0.0,
    progress=None,
    probe_s: float = IDLE_POLL_S,
) -> dict:
    """Wait until `pid` exits, stops moving for `idle_s`, or `timeout_s` expires.

    `progress` returns a comparable picture of the turn's signs of life: a changing
    value is movement, a value that stays equal is stillness. With no callable the
    idle check is off, which is what a guardian with nothing to read does.
    """
    target = _verified_process(pid, created)
    if target is None:
        return {"reason": "sconosciuto", "killed": [], "spared": [], "refused": []}

    descendants: dict[int, float] = {}
    started = clock()
    deadline = started + max(0.0, timeout_s)
    last_move = started
    signature = _progress(progress)
    next_probe = started + probe_s
    reason = "finito"
    while _is_alive(target):
        _record_children(target, descendants)
        now = clock()
        if idle_s > 0 and progress is not None and now >= next_probe:
            current = _progress(progress)
            if current != signature:
                signature = current
                last_move = now
            next_probe = now + probe_s
        if idle_s > 0 and now - last_move >= idle_s:
            reason = IDLE_REASON
            break
        remaining = deadline - now
        if remaining <= 0:
            reason = "scaduto"
            break
        step = min(poll_s, remaining) if poll_s > 0 else 0.0
        sleep(step)

    candidates = _collect_candidates(pid, created, descendants, job)
    targets, spared, refused = _filter_candidates(candidates)
    killed, term_refused = _terminate_targets(targets, grace_s)
    refused.extend(term_refused)
    result = {"reason": reason, "killed": killed, "spared": spared, "refused": refused}
    if reason == IDLE_REASON:
        result["idle_s"] = round(clock() - last_move, 1)
    return result


def _progress(progress) -> object:
    """One progress reading, or None when the reader itself failed: failure is not movement."""
    if progress is None:
        return None
    try:
        return progress()
    except Exception:  # nexgen: allow reason="un lettore dei segni di vita che fallisce non deve uccidere il guardiano"
        return None


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


def _cpu_of(proc: psutil.Process) -> float:
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


def _verified_process(pid: int, created: float) -> psutil.Process | None:
    """Return `psutil.Process(pid)` only if it exists and its `create_time` matches `created`."""
    if pid <= 0:
        return None
    try:
        proc = psutil.Process(pid)
        actual = proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="un pid assente o inaccessibile non va toccato"
        return None
    if abs(actual - created) > _CREATED_SLACK_S:
        return None
    return proc


def _is_alive(proc: psutil.Process) -> bool:
    """Whether `proc` is still running (zombies on POSIX are already finished)."""
    try:
        if not proc.is_running():
            return False
        return proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:  # nexgen: allow reason="un processo che esce mentre lo interroghiamo è già finito"
        return False
    except psutil.AccessDenied:  # nexgen: allow reason="un processo vivo con stato non leggibile resta considerato attivo"
        return True


def _record_children(target: psutil.Process, seen: dict[int, float]) -> None:
    """Record living recursive descendants' `pid -> create_time` before the parent can exit."""
    try:
        children = target.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="il padre può uscire o negare l'accesso tra un controllo e la lettura dei figli"
        return
    self_pid = os.getpid()
    for child in children:
        if child.pid == self_pid:
            continue
        try:
            seen[child.pid] = child.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="un figlio può uscire mentre ne leggiamo il create_time"
            continue


def _collect_candidates(
    pid: int, created: float, descendants: dict[int, float], job: str,
) -> list[tuple[int, float | None]]:
    """Gather `(pid, expected_created_or_none)` across tracked descendants, pgid/job, and root."""
    candidates: list[tuple[int, float | None]] = []
    seen_pids: set[int] = set()
    self_pid = os.getpid()

    def _add(cand_pid: int, expected_created: float | None) -> None:
        if cand_pid != self_pid and cand_pid not in seen_pids:
            seen_pids.add(cand_pid)
            candidates.append((cand_pid, expected_created))

    for dpid, dcreated in reversed(list(descendants.items())):
        _add(dpid, dcreated)
    if os.name != "nt" and hasattr(os, "getpgid"):
        for proc in psutil.process_iter(["pid"]):
            p_pid = proc.pid
            try:
                if os.getpgid(p_pid) == pid:
                    _add(p_pid, None)
            except OSError:  # nexgen: allow reason="un processo può uscire o negare getpgid durante la scansione"
                continue
    if os.name == "nt" and job:
        try:
            for member_pid in jobs.held(job) or []:
                _add(member_pid, None)
        except jobs.JobError:  # nexgen: allow reason="un errore nel leggere il job non deve fermare la pulizia dei pid già noti"
            pass
    _add(pid, created)
    return candidates


def _filter_candidates(
    candidates: list[tuple[int, float | None]],
) -> tuple[list[psutil.Process], list[int], list[int]]:
    """Filter live candidates into `(targets, spared_shared_servers, refused)`."""
    checked: list[tuple[psutil.Process, bool]] = []
    spared: list[int] = []
    protected: set[int] = set()
    refused: list[int] = []
    for cand_pid, expected_created in candidates:
        try:
            proc = psutil.Process(cand_pid)
            if not _is_alive(proc):
                continue
            if expected_created is not None and abs(proc.create_time() - expected_created) > _CREATED_SLACK_S:
                continue
        except psutil.NoSuchProcess:  # nexgen: allow reason="un candidato già finito prima della pulizia non va toccato"
            continue
        except psutil.AccessDenied:  # nexgen: allow reason="un processo non apribile finisce tra i rifiutati senza crash"
            refused.append(cand_pid)
            continue
        shared = processes.is_shared_server(_process_row(proc))
        if shared:
            spared.append(cand_pid)
            protected.update(_shared_descendant_pids(proc))
        checked.append((proc, shared))
    targets = [proc for proc, shared in checked if not shared and proc.pid not in protected]
    return targets, spared, refused


def _shared_descendant_pids(proc: psutil.Process) -> set[int]:
    """Pids of live descendants under a spared shared server, so its subtree stays intact."""
    try:
        return {child.pid for child in proc.children(recursive=True)}
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="un server condiviso che esce o nega i figli non ha discendenti da proteggere"
        return set()


def _process_row(proc: psutil.Process) -> dict:
    """Build a row compatible with `supervision_processes.is_shared_server`."""
    try:
        name = proc.name() or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="un processo che esce o nega il nome produce comunque una riga sicura"
        name = ""
    try:
        cmdline = " ".join(proc.cmdline() or ())
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="un processo che esce o nega la command line produce comunque una riga sicura"
        cmdline = ""
    return {"pid": proc.pid, "name": name, "command_line": cmdline}


def _terminate_targets(targets: list[psutil.Process], grace_s: float) -> tuple[list[int], list[int]]:
    """Send `terminate()`, wait `grace_s`, then `kill()` survivors; return `(killed, refused)`."""
    terminating: list[psutil.Process] = []
    refused: list[int] = []
    for proc in targets:
        try:
            proc.terminate()
            terminating.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="processo già finito o con accesso negato alla chiusura"
            refused.append(proc.pid)

    if not terminating:
        return [], refused

    gone, alive = psutil.wait_procs(terminating, timeout=max(0.0, grace_s))
    killed = [proc.pid for proc in gone]
    force_killed: list[psutil.Process] = []
    for proc in alive:
        try:
            proc.kill()
            force_killed.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # nexgen: allow reason="processo già finito o con accesso negato al kill finale"
            refused.append(proc.pid)

    if force_killed:
        gone_after, still_alive = psutil.wait_procs(force_killed, timeout=min(1.0, max(0.1, grace_s)))
        killed.extend(proc.pid for proc in gone_after)
        refused.extend(proc.pid for proc in still_alive)
    return killed, refused


def guard(
    task_id: str, unit: str, pid: int, created: float, *,
    timeout_s: float = DEFAULT_TIMEOUT_S, job: str = "", idle_s: float = 0.0,
    poll_s: float = POLL_S, grace_s: float = GRACE_S, launch: dict | None = None,
    attempt: int = 1, progress=None, reap_fn=None, relaunch_fn=None, hold_fn=None,
    alert_fn=None,
) -> dict:
    """Watch one turn and carry it through an idle stop: one retry, then the stall.

    The retry is the point of the whole state machine: a turn that hangs before it
    reads anything is usually a cold start that the same command can survive, and a
    person should not have to relaunch it by hand. The second idle stop is a real
    stall: the unit is marked in the journal and the owner is told once.
    """
    result = (reap_fn or reap)(
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


def _launch_of(raw: str) -> dict | None:
    """The relaunch recipe as the supervisor wrote it, or None when it does not parse."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:  # nexgen: allow reason="una ricetta illeggibile si comporta come nessuna ricetta: lo stallo viene detto lo stesso"
        return None
    return parsed if isinstance(parsed, dict) else None


def _probe_for(pid: int, launch: dict | None):
    """The signs-of-life reader for this turn: everything the recipe names, CPU alone otherwise."""
    folder = str((launch or {}).get("folder") or "")
    logs = [str(path) for path in (launch or {}).get("logs") or []]
    return lambda: signs_of_life(pid, folder, logs)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the detached guardian process."""
    parser = argparse.ArgumentParser(prog="nexgen_local.supervision_reaper")
    parser.add_argument("--task", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--created", type=float, required=True)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--job", default="")
    parser.add_argument("--poll", type=float, default=POLL_S)
    parser.add_argument("--grace", type=float, default=GRACE_S)
    parser.add_argument("--idle", type=float, default=0.0)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--launch", default="")
    args = parser.parse_args(argv)

    launch = _launch_of(args.launch)
    # Every idle guardian reads the CPU time, and the folder and logs when the recipe names them.
    progress = _probe_for(args.pid, launch) if args.idle > 0 else None
    guard(
        args.task, args.unit, args.pid, args.created,
        timeout_s=args.timeout, job=args.job, idle_s=args.idle,
        poll_s=args.poll, grace_s=args.grace, launch=launch, attempt=args.attempt,
        progress=progress,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
