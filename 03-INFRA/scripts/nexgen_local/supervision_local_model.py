"""The local model's server: start it when a local unit runs, stop it when the work is done.

One job: own the lifecycle of the `llama-server` the lane declares, so a local
unit is not a manual procedure. The start command, the port, the health path and
the free-memory thresholds live in the lane's configuration, never here: this
module validates them, refuses to start when the machine cannot hold the model,
waits for `/health`, and records in the journal that the engine started the
server. `sup collect` calls `release`, which stops a server the engine started
and nobody else is using, and leaves alone a server the owner started by hand.

Nothing is guessed: a threshold that cannot be read is a refusal, not a pass, and
a server that does not answer is stopped again before the error is raised.
"""
from __future__ import annotations

import ctypes
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nexgen_core.processes import no_window_kwargs, windows_command_argv

from . import supervision_policy as policy
from . import supervision_processes as processes
from . import supervision_state as state
from .supervision_state import SupervisionError

#: Where the engine remembers the server it started: one per machine, beside the lane.
MARKER_FILE = "local-model-server.json"
#: The health path the lane overrides when its server exposes another one.
DEFAULT_HEALTH_PATH = "/health"
#: Free memory a local model needs before the engine starts it.
DEFAULT_RAM_FREE_GB = 2.0
#: A VRAM threshold is only checked when the lane declares one above zero.
DEFAULT_VRAM_FREE_GB = 0.0
#: How long the server gets to answer `/health`, and to die after being stopped.
DEFAULT_START_TIMEOUT_S = 180.0
DEFAULT_STOP_TIMEOUT_S = 30.0
POLL_S = 1.0
#: The events the journal keeps about the server, so a later reader knows who started it.
STARTED_EVENT = "local_server_started"
STOPPED_EVENT = "local_server_stopped"
#: A unit in one of these states is done with its server.
FINISHED_STATES = ("pubblicata", "pubblicata in locale", "raccolta", "ritirata", "revisione")


class LocalModelError(SupervisionError):
    """The lane declares no usable server, or the machine cannot hold the model now."""


@dataclass(frozen=True)
class Profile:
    """The lane's local-model settings, validated, with the one URL the check asks."""

    model: str
    command: tuple[str, ...]
    health: str
    ram_free_gb: float
    vram_free_gb: float
    start_timeout_s: float
    stop_timeout_s: float


def profile(path: str | Path | None = None) -> Profile:
    """The `supervision.local_model` section as a validated profile.

    The server's command is the only field without a default: a lane that names a
    model but not how to serve it cannot be started, and the refusal says what to
    write instead of guessing an executable.
    """
    raw = policy.local_model(path)
    command = _command(raw.get("command"))
    port = _positive_int(raw.get("port"), 0)
    health = str(raw.get("health") or "").strip()
    if not health:
        if not port:
            raise LocalModelError(
                "`supervision.local_model` senza `health` né `port`: scrivi "
                "l'indirizzo del controllo o la porta del server"
            )
        health = f"http://127.0.0.1:{port}{DEFAULT_HEALTH_PATH}"
    return Profile(
        model=str(raw.get("model") or "").strip(),
        command=command,
        health=health,
        ram_free_gb=_threshold(raw.get("ram_free_gb"), DEFAULT_RAM_FREE_GB, "ram_free_gb"),
        vram_free_gb=_threshold(raw.get("vram_free_gb"), DEFAULT_VRAM_FREE_GB, "vram_free_gb"),
        start_timeout_s=_threshold(raw.get("start_timeout_s"), DEFAULT_START_TIMEOUT_S, "start_timeout_s"),
        stop_timeout_s=_threshold(raw.get("stop_timeout_s"), DEFAULT_STOP_TIMEOUT_S, "stop_timeout_s"),
    )


def _command(raw) -> tuple[str, ...]:
    """The start command as an argv list; a string is split, never handed to a shell."""
    if isinstance(raw, str) and raw.strip():
        return tuple(shlex.split(raw, posix=os.name != "nt"))
    if isinstance(raw, (list, tuple)) and raw and all(isinstance(item, str) for item in raw):
        return tuple(raw)
    raise LocalModelError(
        "`supervision.local_model.command` deve essere la lista degli argomenti del server "
        "(es. [\"llama-server\", \"-m\", \"<modello>\", \"--port\", \"8080\"])"
    )


def _positive_int(value, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LocalModelError(f"`supervision.local_model.port` deve essere una porta: {value!r}")
    return value


def _threshold(value, default: float, name: str) -> float:
    """A threshold is a non-negative number; a string is a configuration mistake, not zero."""
    if value is None:
        return float(default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise LocalModelError(f"`supervision.local_model.{name}` deve essere un numero non negativo: {value!r}")
    return float(value)


def free_ram_gb() -> float:
    """How much RAM this machine can hand out now, in GB; unreadable is an error.

    Windows answers through `GlobalMemoryStatusEx`, Linux through `/proc/meminfo`'s
    `MemAvailable`: both say what a new process could take without swapping, which
    is the only number that matters before loading a model. A platform where
    neither exists refuses instead of assuming there is room.
    """
    if os.name == "nt":
        return _windows_free_ram_gb()
    return _linux_free_ram_gb()


def _windows_free_ram_gb() -> float:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("load", ctypes.c_ulong),
            ("total", ctypes.c_ulonglong),
            ("available", ctypes.c_ulonglong),
            ("total_page", ctypes.c_ulonglong),
            ("available_page", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        raise LocalModelError("memoria libera non leggibile: GlobalMemoryStatusEx ha rifiutato")
    return round(float(status.available) / (1024 ** 3), 3)


def _linux_free_ram_gb() -> float:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError as exc:
        raise LocalModelError(f"memoria libera non leggibile: {exc}") from exc
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return round(int(parts[1]) / (1024 ** 2), 3)
    raise LocalModelError("memoria libera non leggibile: /proc/meminfo senza MemAvailable")


def free_vram_gb() -> float | None:
    """Free VRAM of the first GPU, or None when the machine reports no such number."""
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        done = subprocess.run(
            [executable, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=30.0, check=False, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):  # nexgen: allow reason="una macchina senza nvidia-smi non ha una VRAM da leggere: la soglia a zero la salta"
        return None
    if done.returncode:
        return None
    for line in (done.stdout or "").splitlines():
        try:
            return round(float(line.strip()) / 1024, 3)
        except ValueError:  # nexgen: allow reason="una riga di nvidia-smi non numerica non è una misura di VRAM"
            continue
    return None


def check_memory(
    profile_or_none: Profile | None = None, *, ram_reader=None, vram_reader=None,
) -> list[str]:
    """Refuse when RAM (or a declared VRAM threshold) cannot hold the model, else say the room.

    A declared VRAM threshold on a machine whose VRAM cannot be read is a refusal:
    the check exists to protect the owner's GPU, and "unknown" does not protect it.
    """
    chosen = profile_or_none or profile()
    ram_reader = ram_reader or free_ram_gb
    try:
        free_ram = float(ram_reader())
    except (OSError, ValueError, TypeError, LocalModelError) as exc:
        raise LocalModelError(f"memoria RAM non verificabile, non avvio il server: {exc}") from exc
    if free_ram < chosen.ram_free_gb:
        raise LocalModelError(
            f"RAM libera {free_ram:.1f} GB sotto la soglia di {chosen.ram_free_gb:.1f} GB: "
            "chiudi qualcosa, oppure abbassa `supervision.local_model.ram_free_gb`"
        )
    notes = [f"RAM libera {free_ram:.1f} GB (soglia {chosen.ram_free_gb:.1f} GB)"]
    if chosen.vram_free_gb > 0:
        vram_reader = vram_reader or free_vram_gb
        free_vram = vram_reader()
        if free_vram is None:
            raise LocalModelError(
                "VRAM non leggibile mentre la lane chiede "
                f"{chosen.vram_free_gb:.1f} GB liberi: non avvio il server"
            )
        if float(free_vram) < chosen.vram_free_gb:
            raise LocalModelError(
                f"VRAM libera {float(free_vram):.1f} GB sotto la soglia di "
                f"{chosen.vram_free_gb:.1f} GB: chiudi l'altro modello, oppure abbassa la soglia"
            )
        notes.append(f"VRAM libera {float(free_vram):.1f} GB (soglia {chosen.vram_free_gb:.1f} GB)")
    return notes


def health_ok(profile_or_none: Profile | None = None, *, opener=None) -> bool:
    """Whether the server answers its own health check; any failure is "not up"."""
    chosen = profile_or_none or profile()
    import urllib.error
    import urllib.request

    try:
        with (opener or urllib.request.urlopen)(chosen.health, timeout=3) as answer:
            return 200 <= int(getattr(answer, "status", 0) or 0) < 300
    except (urllib.error.URLError, OSError, ValueError):  # nexgen: allow reason="un server che non risponde a /health non è acceso: è la domanda che questa funzione fa"
        return False


def ensure_running(
    task_id: str, unit: str, *, profile_reader=None, probe=None, spawn=None, memory=None,
    clock=None, sleep=None,
) -> list[str]:
    """Start the lane's server for this unit when it is down, and say what happened.

    A server already answering is used as it is and is not marked as the engine's
    own: `release` must never stop what the owner started. The marker is written
    only after the process exists, and removed again when `/health` never answers,
    so a failed start leaves nothing behind that a later collect would kill.
    """
    chosen = (profile_reader or profile)()
    is_up = probe or health_ok
    if is_up(chosen):
        return [f"modello locale: server già acceso su {chosen.health}"]
    notes = (memory or check_memory)(chosen)
    now, pause = clock or time.monotonic, sleep or time.sleep
    process = (spawn or _spawn)(chosen)
    _write_marker(_marker_row(task_id, unit, chosen, getattr(process, "pid", None)))
    if _wait_healthy(chosen, process, is_up, now, pause):
        state.append_event(
            task_id, STARTED_EVENT, unit=unit, pid=getattr(process, "pid", None),
            model=chosen.model, health=chosen.health, command=list(chosen.command),
        )
        return notes + [f"modello locale: server avviato dal motore (pid {getattr(process, 'pid', '?')})"]
    _stop_quietly(process)
    _drop_marker()
    raise LocalModelError(
        f"il server locale non risponde su {chosen.health} entro {chosen.start_timeout_s:.0f}s: "
        f"comando `{' '.join(chosen.command)}`, fermato di nuovo"
    )


def _spawn(chosen: Profile):
    """Start the server detached, so the command that started it can return."""
    argv = windows_command_argv(list(chosen.command))
    try:
        return subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **_detach_kwargs(),
        )
    except OSError as exc:
        raise LocalModelError(f"server locale non avviato ({type(exc).__name__}): {chosen.command[0]}") from exc


def _detach_kwargs() -> dict:
    if sys.platform.startswith(("win", "cygwin", "msys")):
        return no_window_kwargs()
    return {"start_new_session": True}


def _wait_healthy(chosen: Profile, process, is_up, now, pause) -> bool:
    """Poll `/health` until it answers, the budget ends, or the process died first."""
    deadline = now() + chosen.start_timeout_s
    while True:
        if is_up(chosen):
            return True
        exit_code = _exit_code(process)
        if exit_code is not None:
            return False
        if now() >= deadline:
            return False
        pause(POLL_S)


def _exit_code(process) -> int | None:
    """The exit code when this process is already gone; a live or fake one has none."""
    poll = getattr(process, "poll", None)
    return poll() if callable(poll) else None


def release(
    task_id: str = "", unit: str = "", *, marker_reader=None, probe=None, killer=None,
    clock=None, sleep=None, stopper=None,
) -> list[str]:
    """Stop the server the engine started for this unit, when nobody else is using it.

    A server whose marker names another unit that has not finished stays up: one
    machine, one local model, and two units cannot share it by accident. A marker
    that names no unit at all is the engine's own leftover and is stopped. The
    marker is removed only once the health check stops answering.
    """
    row = (marker_reader or _read_marker)()
    if not row:
        return []
    holder = (str(row.get("task") or ""), str(row.get("unit") or ""))
    if holder != (task_id, unit) and _still_used(holder, task_id, unit):
        return [f"modello locale: server tenuto acceso, lo usa {holder[0]}/{holder[1]}"]
    chosen = _profile_for_marker(row)
    if chosen is None:
        return [f"modello locale: server del marcatore {MARKER_FILE} non più descritto dalla lane: "
                f"pid {row.get('pid')} lasciato acceso"]
    stop = stopper or _stop
    stopped, line = stop(row, chosen, probe or health_ok, killer or processes.kill, clock, sleep)
    if stopped:
        _drop_marker()
        if task_id:
            state.append_event(
                task_id, STOPPED_EVENT, unit=unit or None, pid=row.get("pid"),
                health=chosen.health, why="nessun'altra unità locale lo usa",
            )
    return [line]


def _stop(row: dict, chosen: Profile, is_up, kill, clock, sleep) -> tuple[bool, str]:
    """Stop the recorded pid, then wait for a health check that no longer answers."""
    pid = row.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return True, "modello locale: marcatore senza pid, niente da fermare"
    try:
        kill([pid])
    except (OSError, processes.ProcessError) as exc:
        return False, f"modello locale: server pid {pid} non fermato ({exc})"
    now, pause = clock or time.monotonic, sleep or time.sleep
    deadline = now() + chosen.stop_timeout_s
    while now() < deadline:
        if not is_up(chosen):
            return True, f"modello locale: server fermato (pid {pid})"
        pause(POLL_S)
    return False, f"modello locale: pid {pid} fermato ma {chosen.health} risponde ancora"


def _still_used(holder: tuple[str, str], task_id: str, unit: str) -> bool:
    """Whether another unit in the same task still runs on the local model."""
    holder_task, holder_unit = holder
    if not holder_task or holder_task != task_id:
        return bool(holder_task)
    if not holder_unit or holder_unit == unit:
        return False
    try:
        record = state.unit_record(holder_task, holder_unit)
    except SupervisionError:  # nexgen: allow reason="un'unità che la corsia non apre più non tiene il server"
        return False
    return str(record.get("state") or "") not in FINISHED_STATES


def _profile_for_marker(row: dict) -> Profile | None:
    """The profile the marker was written with, when today's lane still declares one."""
    try:
        chosen = profile()
    except (LocalModelError, SupervisionError):  # nexgen: allow reason="un profilo locale non leggibile non può dire di essere lo stesso server"
        return None
    if row.get("health") and str(row["health"]) != chosen.health:
        return None
    return chosen


def _marker_row(task_id: str, unit: str, chosen: Profile, pid) -> dict:
    return {
        "pid": pid, "task": task_id, "unit": unit, "model": chosen.model,
        "health": chosen.health, "port": _port_of(chosen.health),
        "command": list(chosen.command),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "started_by": "engine",
    }


def _port_of(health: str) -> int:
    """The port in the health URL, so a listing can say where the server listens."""
    rest = health.split("://", 1)[-1]
    host = rest.split("/", 1)[0]
    tail = host.rsplit(":", 1)
    return int(tail[1]) if len(tail) == 2 and tail[1].isdigit() else 0


def _marker_path() -> Path:
    return state.lane_state_dir() / MARKER_FILE


def _write_marker(row: dict) -> None:
    path = _marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_marker() -> dict:
    """The marker of the server the engine started, or an empty row when there is none."""
    path = _marker_path()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="un marcatore illeggibile è l'assenza di un server avviato dal motore"
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _drop_marker() -> None:
    try:
        _marker_path().unlink()
    except FileNotFoundError:  # nexgen: allow reason="un marcatore già assente è lo stato che si voleva ottenere"
        return


def _stop_quietly(process) -> None:
    """Kill a server this call just started and could not reach; a dead one is already stopped."""
    kill = getattr(process, "kill", None)
    if callable(kill):
        try:
            kill()
        except OSError:  # nexgen: allow reason="un processo già morto non ha bisogno di essere fermato"
            return


def describe() -> str:
    """One line for `sup status`: where the server is and who started it, or that none is up."""
    row = _read_marker()
    if row:
        return (f"modello locale: server avviato dal motore il {row.get('started_at', '?')} "
                f"per {row.get('task', '?')}/{row.get('unit', '?')} su {row.get('health', '?')}")
    try:
        chosen = profile()
    except (LocalModelError, SupervisionError) as exc:
        return f"modello locale: non configurato ({exc})"
    return f"modello locale: nessun server avviato dal motore ({chosen.health})"


def release_for_unit(task_id: str, unit: str, record: dict) -> list[str]:
    """Stop this unit's server when the unit ran locally; anything else has no server of ours.

    Collect and retirement both call this, so a local unit leaves no server behind
    whichever verb closes it, and a unit on another harness never looks for one.
    """
    if str(record.get("harness") or "") != "local":
        return []
    try:
        return release(task_id, unit)
    except SupervisionError as exc:
        return [f"modello locale: server non fermato ({exc})"]
