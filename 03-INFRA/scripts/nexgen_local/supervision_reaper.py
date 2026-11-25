"""Detached per-turn guardian that waits for a harness turn and cleans up its tree.

One job: watch one detached turn process until it exits or its timeout expires,
then terminate every descendant, process-group member or Windows job member it
left behind while sparing the shared `opencode serve` and never touching a
recycled pid.
"""
from __future__ import annotations

import argparse
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
_CREATED_SLACK_S = 0.5
_REAL_POPEN = subprocess.Popen  # captured at import, before a test replaces it


def watchable(proc) -> bool:
    """Whether this is a process this engine really started, and so one a guardian may watch."""
    return isinstance(proc, _REAL_POPEN)


def watch(task_id: str, unit: str, proc, *, timeout_s: float = DEFAULT_TIMEOUT_S, job: str = "") -> str:
    """Spawn a detached windowless guardian for `proc`, or return an empty string for test doubles."""
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
    ]
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
) -> dict:
    """Wait until `pid` exits or `timeout_s` expires, then stop its remaining process tree."""
    target = _verified_process(pid, created)
    if target is None:
        return {"reason": "sconosciuto", "killed": [], "spared": [], "refused": []}

    descendants: dict[int, float] = {}
    deadline = clock() + max(0.0, timeout_s)
    reason = "finito"
    while _is_alive(target):
        _record_children(target, descendants)
        remaining = deadline - clock()
        if remaining <= 0:
            reason = "scaduto"
            break
        step = min(poll_s, remaining) if poll_s > 0 else 0.0
        sleep(step)

    candidates = _collect_candidates(pid, created, descendants, job)
    targets, spared, refused = _filter_candidates(candidates)
    killed, term_refused = _terminate_targets(targets, grace_s)
    refused.extend(term_refused)
    return {"reason": reason, "killed": killed, "spared": spared, "refused": refused}


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
    args = parser.parse_args(argv)

    result = reap(
        args.pid, args.created,
        timeout_s=args.timeout, job=args.job, poll_s=args.poll, grace_s=args.grace,
    )
    try:
        state.append_event(
            args.task, "turn_reaped",
            unit=args.unit, pid=args.pid, reason=result["reason"],
            killed=result["killed"], spared=result["spared"], refused=result.get("refused", []),
        )
    except (OSError, state.SupervisionError):  # nexgen: allow reason="un diario non scrivibile o assente non deve far fallire la pulizia"
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
