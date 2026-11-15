"""The automation jobs OpenClaw schedules, and the proactive check among them.

One job: read the job list the Gateway scheduler prints and recognise the
"Proactive check" monitor, so the setup can switch that job off and the doctor
can judge it instead of judging a key nobody reads.

Why this exists. A heartbeat used to be a configuration key (`agents.defaults.heartbeat`)
and it is an ordinary automation job in the pinned release: the Gateway page says
Doctor removes the legacy settings "only after their canonical jobs have been
persisted and verified", and that the job OpenClaw provisions "runs every 30
minutes unless the provider supplies a different initial cadence, uses the
agent's main conversation, waits for idle execution, and delivers to a resolved
owner DM". A job in the main conversation re-sends whatever that conversation has
grown to, 48 times a day, for a bot that may be answering nobody: the engine's
own `heartbeat.every: 0m` switches off a key the release no longer reads.

The job list is read, never written here: the only supported writer is the CLI
(`openclaw automations ...`, whose alias is `openclaw cron ...`), and the shape
this module reads is the documented JSON of `automations list --all --json`,
where every row carries `id`, `name`, a `schedule` of `at`, `every` or `cron`,
`enabled`, and a top-level `status` derived from them.

A job is recognised as the monitor by its name, display name or declaration key:
OpenClaw names the provisioned one "Proactive check", and a migrated monitor
keeps the name a person can edit. That is a heuristic on purpose, because the
stable id is generated per installation and the pinned page names no field that
marks a job as a heartbeat.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw_service import RunResult

#: Words that name the monitor in a job's name, display name or declaration key.
MATCH_WORDS = ("proactive", "heartbeat")
#: The declaration key the engine writes for its own hourly context guard. `setup`
#: registers the job with it, and the doctor reads it back by the same value, so the
#: writer and the reader cannot drift apart.
CONTEXT_GUARD_DECLARATION_KEY = "nexgen:context-guard"
#: A scheduler that does not answer within this many seconds is asked again later.
CLI_TIMEOUT_SECONDS = 20
#: The slowest a cheap check may run and still be cheap: four runs a day, the cadence
#: `nexgen openclaw setup` converges a light check to.
LIGHT_MAX_MINUTES = 6 * 60

#: `(argv, env)`: the executor a test replaces with a recorder.
Run = Callable[[list[str], Mapping[str, str] | None], RunResult]
#: What the doctor asks: every job the scheduler lists, or None when it cannot say.
Reader = Callable[[], "list[dict[str, Any]] | None"]


def _texts(job: Mapping[str, Any]) -> list[str]:
    """The strings a job may carry its name in, lowercased."""
    fields = ("name", "displayName", "declarationKey", "description")
    return [str(job.get(field) or "").lower() for field in fields]


def is_heartbeat_job(job: Any) -> bool:
    """Whether this job is the proactive check a heartbeat became."""
    if not isinstance(job, Mapping):
        return False
    return any(word in text for text in _texts(job) for word in MATCH_WORDS)


def heartbeat_jobs(jobs: Any) -> list[dict[str, Any]]:
    """Every job in the list that is the proactive check, enabled or not."""
    return [dict(job) for job in jobs if is_heartbeat_job(job)] if isinstance(jobs, list) else []


def is_enabled(job: Mapping[str, Any]) -> bool:
    """Whether the scheduler would run this job: a row that does not say is taken as on."""
    return job.get("enabled") is not False and job.get("status") != "disabled"


def enabled_heartbeat_jobs(jobs: Any) -> list[dict[str, Any]]:
    """The proactive-check jobs that would run, which are the ones costing model turns."""
    return [job for job in heartbeat_jobs(jobs) if is_enabled(job)]


def context_guard_jobs(jobs: Any) -> list[dict[str, Any]]:
    """Every job the engine declared as its hourly context guard."""
    if not isinstance(jobs, list):
        return []
    return [
        dict(job) for job in jobs
        if isinstance(job, Mapping) and job.get("declarationKey") == CONTEXT_GUARD_DECLARATION_KEY
    ]


def delivery_mode(job: Mapping[str, Any]) -> str | None:
    """The delivery the scheduler stored for this job (`none`, `announce`, `webhook`), or None.

    The pinned CLI writes `delivery: {mode: ...}`: `--no-deliver` on a command job stores
    `none`, and a job declared without it stores `announce`, which makes the gateway
    record every hourly run as a failed delivery.
    """
    delivery = job.get("delivery") if isinstance(job.get("delivery"), Mapping) else {}
    mode = delivery.get("mode")
    return mode.strip() if isinstance(mode, str) and mode.strip() else None


def job_id(job: Mapping[str, Any]) -> str:
    """The id the CLI takes: `id`, or the `jobId` an older store wrote."""
    return str(job.get("id") or job.get("jobId") or "")


def job_name(job: Mapping[str, Any]) -> str:
    """The name a person reads, falling back to the id when the row carries none."""
    return str(job.get("displayName") or job.get("name") or job_id(job) or t("(unnamed job)"))


def schedule_text(job: Mapping[str, Any]) -> str:
    """The cadence as the job stores it: a cron expression, an interval, or a one-shot time."""
    schedule = job.get("schedule") if isinstance(job.get("schedule"), Mapping) else {}
    for kind in ("cron", "every", "at"):
        value = schedule.get(kind)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return t("(no schedule)")


def job_payload(job: Mapping[str, Any]) -> dict[str, Any]:
    """The payload the job carries: the kind of run, its tools, and its bootstrap mode."""
    body = job.get("payload") if isinstance(job.get("payload"), Mapping) else {}
    return dict(body)


def session_target(job: Mapping[str, Any]) -> str:
    """The session the job runs in (`main`, `isolated`, `current`, `session:<id>`)."""
    return str(job.get("sessionTarget") or job.get("session") or "").strip()


def runs_light(job: Mapping[str, Any]) -> bool:
    """Whether a run costs one small call instead of a turn over the whole conversation.

    All three flags are what `openclaw automations edit` writes for a cheap check, and
    all three are documented for the pinned release: `--session isolated` keeps the run
    out of the agent's conversation, `--light-context` skips the workspace bootstrap
    files (documented for isolated jobs only), and `--tools ""` disables every agent
    tool. A row that says nothing about them is not light: the honest answer is the
    expensive one until the scheduler says otherwise.
    """
    body = job_payload(job)
    return (
        session_target(job) == "isolated"
        and body.get("lightContext") is True
        and body.get("toolsAllow") == []
    )


#: An interval as the CLI stores it: `30m`, `6h`, `1d`, `90s`.
_INTERVAL = re.compile(r"^\s*(\d+)\s*(s|m|h|d)\s*$")
#: A cron step in one field: `*/30`.
_STEP = re.compile(r"^\*/(\d+)$")
#: Minutes per unit of `_INTERVAL`.
_UNIT_MINUTES = {"s": 1 / 60, "m": 1, "h": 60, "d": 1440}


def _cron_minutes(expression: str) -> int | None:
    """The minutes between two runs of a five-field cron, for the few shapes worth reading."""
    fields = expression.split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        return None
    minute, hour = fields[0], fields[1]
    step = _STEP.match(minute)
    if step and hour == "*":
        return int(step.group(1))
    if minute.isdigit() and hour == "*":
        return 60
    hour_step = _STEP.match(hour)
    if minute.isdigit() and hour_step:
        return int(hour_step.group(1)) * 60
    if minute.isdigit() and hour.isdigit():
        return 1440
    return None


def cadence_minutes(job: Mapping[str, Any]) -> int | None:
    """How many minutes apart two runs are, or None when this is not a shape it reads.

    Only what a monitor uses: an interval, a minute step, an hourly tick, an hour step,
    or one fixed time a day. A calendar schedule, a one-shot `at` or anything else is
    unknown rather than guessed, and an unknown cadence never becomes a warning.
    """
    schedule = job.get("schedule") if isinstance(job.get("schedule"), Mapping) else {}
    every = schedule.get("every")
    if isinstance(every, str):
        match = _INTERVAL.match(every)
        if match:
            return max(1, round(int(match.group(1)) * _UNIT_MINUTES[match.group(2)]))
        return None
    cron = schedule.get("cron")
    return _cron_minutes(cron) if isinstance(cron, str) else None


def runs_per_day(job: Mapping[str, Any]) -> int | None:
    """How many times a day this job would run, or None when its cadence cannot be read."""
    minutes = cadence_minutes(job)
    return max(1, round(1440 / minutes)) if minutes else None


def jobs_from_payload(payload: Any) -> list[dict[str, Any]] | None:
    """The job rows in a scheduler answer, or None when the answer is not a job list."""
    if isinstance(payload, Mapping):
        if payload.get("ok") is False:
            return None
        rows = payload.get("jobs", payload)
    else:
        rows = payload
    if not isinstance(rows, list):
        return None
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def jobs_from_stdout(stdout: str | None) -> list[dict[str, Any]] | None:
    """The job rows in what the CLI printed, or None when it printed something else."""
    try:
        payload = json.loads(stdout or "")
    except ValueError:  # nexgen: allow reason="text that is not JSON is a scheduler that did not answer, never an empty job list"
        return None
    return jobs_from_payload(payload)


def _cli_run(argv: list[str], env: Mapping[str, str] | None = None) -> RunResult:
    """Run one scheduler query with a bounded wait; a program that cannot answer is an answer."""
    from nexgen_core.processes import no_window_kwargs

    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            timeout=CLI_TIMEOUT_SECONDS, env=dict(env) if env is not None else None,
            **no_window_kwargs()
        )
    except subprocess.TimeoutExpired:
        return RunResult(1, "", t("timed out after {seconds}s", seconds=CLI_TIMEOUT_SECONDS))
    except (OSError, subprocess.SubprocessError) as exc:  # nexgen: allow reason="a CLI that cannot start is an unreadable scheduler, and an unreadable scheduler is never green"
        return RunResult(1, "", str(exc))
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


def read_jobs(
    *, binary: str | None = None, env: Mapping[str, str] | None = None, run: Run | None = None,
) -> list[dict[str, Any]] | None:
    """Every job the scheduler holds, including disabled ones, or None when it cannot say.

    A scheduler that refuses, hangs or prints something else is one answer: unreadable.
    The doctor reads that as unknown rather than as an empty list, because an empty list
    here would mean "no monitor runs on this machine", which was not proved.
    """
    from nexgen_core.openclaw.sessions_groom import _resolve_binary

    try:
        tool = binary or _resolve_binary()
    except FileNotFoundError:  # nexgen: allow reason="no CLI on this machine is a scheduler that cannot be asked, which the caller reports as unknown"
        return None
    result = (run or _cli_run)([tool, "automations", "list", "--all", "--json"], env)
    if result.returncode != 0:
        return None
    return jobs_from_stdout(result.stdout)


def job_from_payload(payload: Any) -> dict[str, Any] | None:
    """One job row in a `get` answer, or None when that answer is not a job.

    `automations get <id> --json` returns the stored job itself; the CLI also wraps a
    result in `{"ok": true, "job": {...}}` in the shapes it documents, so both are read.
    """
    if not isinstance(payload, Mapping) or payload.get("ok") is False:
        return None
    row = payload.get("job", payload)
    if not isinstance(row, Mapping):
        return None
    return dict(row) if row.get("id") or row.get("jobId") or row.get("name") else None


def read_job(
    job_id: str, *, binary: str | None = None, env: Mapping[str, str] | None = None,
    run: Run | None = None,
) -> dict[str, Any] | None:
    """One job as `automations get <id> --json` prints it, or None when it cannot be read.

    The list a scheduler prints is a summary: whether a job runs light is in its payload,
    which the list may leave out. This is the second, read-only question, asked once per
    monitor and never for every job on the machine.
    """
    from nexgen_core.openclaw.sessions_groom import _resolve_binary

    try:
        tool = binary or _resolve_binary()
    except FileNotFoundError:  # nexgen: allow reason="no CLI on this machine is a scheduler that cannot be asked, which the caller reports as unknown"
        return None
    result = (run or _cli_run)([tool, "automations", "get", job_id, "--json"], env)
    if result.returncode != 0:
        return None
    try:
        detail = json.loads(result.stdout or "")
    except ValueError:  # nexgen: allow reason="text that is not JSON is a scheduler that did not answer, never a job with no fields"
        return None
    return job_from_payload(detail)


def read_jobs_with_detail(
    *, binary: str | None = None, env: Mapping[str, str] | None = None, run: Run | None = None,
) -> list[dict[str, Any]] | None:
    """Every job, with each proactive check replaced by what `automations get` says about it.

    The doctor has to judge the job, not its name: whether it runs in the agent's
    conversation or its own, whether the bootstrap files and the tools come with it, and
    how often. Those live in the payload, so the monitor rows are read again one by one.
    A `get` that fails leaves the summary row in place, and the doctor then says the run
    is not proven light rather than assuming it is.
    """
    from nexgen_core.openclaw.sessions_groom import _resolve_binary

    try:
        tool = binary or _resolve_binary()
    except FileNotFoundError:  # nexgen: allow reason="no CLI on this machine is a scheduler that cannot be asked, which the caller reports as unknown"
        return None
    rows = read_jobs(binary=tool, env=env, run=run)
    if rows is None:
        return None
    detailed: list[dict[str, Any]] = []
    for row in rows:
        if is_heartbeat_job(row) and job_id(row):
            detail = read_job(job_id(row), binary=tool, env=env, run=run)
            if detail is not None:
                row = {**row, **detail}
        detailed.append(row)
    return detailed
