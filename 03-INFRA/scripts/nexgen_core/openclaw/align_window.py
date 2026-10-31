"""One job: decide whether this guard cycle may replace the OpenClaw package here.

Why it exists: `npm install -g` rewrites the files a running program is reading, and the
restart that follows ends whatever that program was doing. A gateway in the middle of an
agent turn, or in the middle of an automation, answers half a sentence to a person who is
waiting for it. So the install waits for a machine that is provably free, and a machine
that cannot prove it is free waits too.

The rules, in the order they are read:

- on the machine that runs the gateway, the night window, 03:00-06:00 local time of this
  machine: that is when nobody is asking it anything;
- on the gateway, no agent session in a busy state (`BUSY_STATES` of the session groomer)
  and no automation executing either: `openclaw cron list --json` marks a job `running`
  while it runs, and older payloads carry `state.runningAtMs`;
- on a node, no window and no list. A night window would mean a PC that is off at night
  never updates, and the session list is the gateway's signal: on a node it fails, because
  the store belongs to the gateway, so waiting for it would postpone the install forever.
  A node is free when its own service is idle, that is when the pid `openclaw node status
  --json` reports (`service.runtime.pid`) has no child process: a command an agent runs on
  the node is a child of that process;
- a package with no service on this machine runs nothing, so it has no job to interrupt and
  waits for nothing;
- an unreadable state is never an idle one: a CLI that fails, hangs or prints something
  that is not JSON, a service that reports no pid, and a pid whose children cannot be read
  all postpone the cycle. Fail closed is the whole point: the cost of a delayed install is
  a day, the cost of a broken turn is a lost answer.

It answers with the sentence a person reads, or with None when the cycle may go ahead, and
it never raises. Every process call and the pid reader are injectable so a test drives
every branch.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.node_status import parse_node_status_payload
from nexgen_core.openclaw.sessions_groom import BUSY_STATES
from nexgen_core.openclaw_service import RunResult

#: Local hours of the window: [03:00, 06:00), read by the gateway alone.
WINDOW_START_HOUR = 3
WINDOW_END_HOUR = 6
#: A status query that never answers must not hold the guard cycle open.
CLI_TIMEOUT_SECONDS = 30
#: Windows has no child list to read, so the process table is asked for the count instead.
WINDOWS_CHILDREN_QUERY = (
    "(Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}' | Measure-Object).Count"
)

#: `(argv, env)` for a program; the shape the setup steps and the version phase use.
Run = Callable[[list[str], Mapping[str, str] | None], RunResult]
#: The sessions the CLI lists, or None when it cannot say.
Sessions = Callable[[str], "list[dict[str, Any]] | None"]
#: A pid and whether it has child processes: True, False, or None when that cannot be read.
Children = Callable[[int], "bool | None"]


def postponement(
    *,
    runs_gateway: bool,
    runs_node: bool,
    moment: float,
    binary: str | None,
    env: Mapping[str, str],
    run: Run | None = None,
    list_sessions: Sessions | None = None,
    platform: str | None = None,
    children: Children | None = None,
) -> str | None:
    """The sentence that says why this cycle waits, or None when it may go ahead.

    A machine with neither service runs nothing, so it waits for nothing either.
    """
    cli = run or _cli_run
    tool = binary or "openclaw"
    if runs_gateway:
        return _gateway_wait(moment, tool, cli, env, list_sessions)
    return _node_wait(tool, cli, env, platform, children) if runs_node else None


def _gateway_wait(moment: float, binary: str, run: Run, env: Mapping[str, str],
                  list_sessions: Sessions | None) -> str | None:
    """A gateway waits for its quiet hours, with no session and no automation in flight."""
    if not _in_window(moment):
        return t("Postponed to the night window (03:00-06:00 local time): the next cycle tries again.")
    lister = list_sessions or (lambda name: _sessions(name, run, env))
    try:
        entries = lister(binary)
    except (OSError, ValueError, subprocess.SubprocessError):  # nexgen: allow reason="an unreadable session list postpones the cycle instead of passing for idle"
        entries = None
    if entries is None:
        return _postponed(t("the work state cannot be read"))
    if any(isinstance(entry, Mapping) and entry.get("status") in BUSY_STATES for entry in entries):
        return _postponed(t("an agent session is busy"))
    busy = _cron_busy(binary, run, env)
    if busy is None:
        return _postponed(t("the work state cannot be read"))
    return _postponed(t("an automation is running")) if busy else None


def _node_wait(binary: str, run: Run, env: Mapping[str, str], platform: str | None,
               children: Children | None) -> str | None:
    """A node waits only for its own service to be idle: its pid with no child process."""
    pid = _running_pid(run([binary, "node", "status", "--json"], env))
    if pid is None:
        return _postponed(t("the work state cannot be read"))
    reader = children or (lambda value: children_present(value, platform=platform, run=run, env=env))
    try:
        busy = reader(pid)
    except (OSError, ValueError, subprocess.SubprocessError):  # nexgen: allow reason="a pid whose children cannot be read postpones the cycle instead of passing for idle"
        busy = None
    if busy is None:
        return _postponed(t("the work state cannot be read"))
    return _postponed(t("a command is running on this node")) if busy else None


def _running_pid(result: RunResult) -> int | None:
    """The pid of the running node service, or None when the CLI does not report one."""
    payload = _json_payload(result)
    if payload is None:
        return None
    status = parse_node_status_payload(payload)
    return status.pid if status.is_running else None


def children_present(
    pid: int, *, platform: str | None = None, proc: Path | None = None,
    run: Run | None = None, env: Mapping[str, str] | None = None,
) -> bool | None:
    """Whether the process `pid` has a child process now: True, False, or None when unknown."""
    system = platform or sys.platform
    cli = run or _cli_run
    values: Mapping[str, str] = env or {}
    if system.startswith("linux"):
        return _proc_children(pid, Path(proc) if proc is not None else Path("/proc"))
    if system == "darwin":
        return _pgrep_children(pid, cli, values)
    if system.startswith("win"):
        return _powershell_children(pid, cli, values)
    return None


def _proc_children(pid: int, proc: Path) -> bool | None:
    """The kernel's own child list of every thread of `pid`, or None when it is gone."""
    listed = sorted((proc / str(pid) / "task").glob("*/children"))
    if not listed:
        return None
    try:
        return any(entry.read_text(encoding="utf-8", errors="replace").strip() for entry in listed)
    except OSError:  # nexgen: allow reason="a thread that ends mid-read leaves the state unknown, and unknown postpones"
        return None


def _pgrep_children(pid: int, run: Run, env: Mapping[str, str]) -> bool | None:
    """Ask `pgrep` for the children of `pid`: exit 1 says there are none."""
    result = run(["pgrep", "-P", str(pid)], env)
    if result.returncode == 0:
        return True
    return False if result.returncode == 1 else None


def _powershell_children(pid: int, run: Run, env: Mapping[str, str]) -> bool | None:
    """Ask the Windows process table for the children of `pid`: a count of zero means none."""
    query = WINDOWS_CHILDREN_QUERY.format(pid=pid)
    result = run(["powershell", "-NoProfile", "-Command", query], env)
    if result.returncode != 0:
        return None
    try:
        return int((result.stdout or "").strip()) > 0
    except ValueError:  # nexgen: allow reason="a count that is not a number leaves the state unknown, and unknown postpones"
        return None


def _postponed(reason: str) -> str:
    """One sentence, the same shape for every reason, so the cycle log reads alike."""
    return t("Postponed: {reason}. The next cycle tries again.", reason=reason)


def _in_window(moment: float) -> bool:
    """Whether this machine's local clock is inside the quiet hours."""
    return WINDOW_START_HOUR <= time.localtime(moment).tm_hour < WINDOW_END_HOUR


def _sessions(binary: str, run: Run, env: Mapping[str, str]) -> list[dict[str, Any]] | None:
    """The stored sessions across agents, or None when the CLI cannot list them."""
    payload = _json_payload(run([binary, "sessions", "list", "--all-agents", "--json", "--limit", "all"], env))
    if payload is None:
        return None
    sessions = payload.get("sessions", payload) if isinstance(payload, Mapping) else payload
    return list(sessions) if isinstance(sessions, list) else None


def _cron_busy(binary: str, run: Run, env: Mapping[str, str]) -> bool | None:
    """Whether an automation is executing now, or None when the scheduler cannot be read."""
    payload = _json_payload(run([binary, "cron", "list", "--all", "--json"], env))
    if payload is None:
        return None
    jobs = payload.get("jobs", payload) if isinstance(payload, Mapping) else payload
    if not isinstance(jobs, list):
        return None
    return any(_job_running(job) for job in jobs)


def _job_running(job: Any) -> bool:
    """One automation is executing: the CLI marks it `running`, or it recorded a start."""
    if not isinstance(job, Mapping):
        return False
    state = job.get("state")
    recorded = state.get("runningAtMs") if isinstance(state, Mapping) else None
    return job.get("status") == "running" or bool(recorded)


def _json_payload(result: RunResult) -> Any | None:
    """The JSON a CLI printed, or None when the call failed or printed something else."""
    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except ValueError:  # nexgen: allow reason="output that is not JSON is an unreadable state, and unreadable postpones"
        return None
    if isinstance(payload, Mapping) and payload.get("ok") is False:
        return None
    return payload


def _cli_run(argv: list[str], env: Mapping[str, str] | None = None) -> RunResult:
    """Run one status query with a bounded wait; a program that cannot answer is an answer."""
    from nexgen_core.processes import no_window_kwargs

    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            timeout=CLI_TIMEOUT_SECONDS, env=dict(env) if env is not None else None,
            **no_window_kwargs()
        )
    except subprocess.TimeoutExpired:
        return RunResult(1, "", t("timed out after {seconds}s", seconds=CLI_TIMEOUT_SECONDS))
    except (OSError, subprocess.SubprocessError) as exc:  # nexgen: allow reason="a program that cannot start postpones the cycle instead of breaking it"
        return RunResult(1, "", str(exc))
    return RunResult(completed.returncode, completed.stdout, completed.stderr)
