"""The doctor line for the hourly context guard: did it run, and what did it leave behind.

One job: judge the hourly groom on two read-only facts -- the job row the
scheduler keeps, and the record its last run wrote -- and name every chat it
cannot shrink.

Why this exists: the groomer's policy, its memory and its plugin patch were all in
place and nothing checked that the job existed, was enabled, or had run at all. A
job switched off in March looks exactly like a fleet with nothing to compact, and
the bill arrives as one 900k-token prompt on the first message after three days
away. The job row answers "did it run"; the record answers "what did it leave",
which no command can ask the scheduler for, because a command job keeps no output.

Two things are named per chat, and only above `IDLE_MIN_TOKENS`, because below the
floor a summary costs more than the cold cache it prevents:
a chat on a lane with no idle compaction at all (an `:acp:` conversation, whose
agent owns a context OpenClaw has no way to ask), and a chat a harness refused to
compact and the memory then stopped retrying (codex answers `compacted: false`
while its agent carries a tools deny list).
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw import groom_memory, groom_run
from nexgen_core.openclaw.groom_policy import HARNESS_RUNTIMES, IDLE_MIN_TOKENS
from nexgen_core.report import CheckOutcome, Severity

CHECK_ID = "openclaw.groom"
#: A guard whose last successful run is older than this has stopped serving the chats.
STALE_SECONDS = 3 * 60 * 60
#: The outcomes after which the memory stops asking a backend that answered nothing.
REFUSED = frozenset({groom_memory.NOT_COMPACTED, groom_memory.UNSUPPORTED})


def _age(seconds: float) -> str:
    """How long ago, in the unit a person reads at a glance."""
    if seconds < 3600:
        return t("{minutes}m", minutes=int(seconds // 60))
    return t("{hours}h", hours=int(seconds // 3600))


def _last_run_ms(job: Mapping[str, Any]) -> float | None:
    """The last run's start, from the millisecond field the CLI prints or the ISO copy beside it."""
    stamp = job.get("lastRunAtMs")
    if isinstance(stamp, (int, float)):
        return float(stamp)
    text = job.get("lastRunAt")
    if isinstance(text, str) and text.strip():
        from datetime import datetime

        try:
            return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).timestamp() * 1000
        except ValueError:  # nexgen: allow reason="a date the scheduler printed in another shape is not a run time, so the line says it has none"
            return None
    return None


def refused_chats(memory: Mapping[str, dict[str, Any]], min_tokens: int = IDLE_MIN_TOKENS) -> list[str]:
    """The chats a harness answered `compacted: false` for, which no later run retries.

    A settled outcome is what stops the hourly retry, so this is the one place those
    chats are still visible: over the idle floor, on a runtime that could yet compact
    them, and stuck until somebody writes to them again.
    """
    found: list[str] = []
    for key, record in sorted(memory.items()):
        runtime = str(record.get("runtime") or "")
        tokens = int((record.get("fingerprint") or {}).get("tokens") or 0)
        if runtime in HARNESS_RUNTIMES and record.get("outcome") in REFUSED and tokens >= min_tokens:
            found.append(t("{key}: {tokens} tokens, OpenClaw answered compacted=false ({runtime})",
                           key=key, tokens=tokens, runtime=runtime))
    return found


def stranded_lines(run: Mapping[str, Any] | None, min_tokens: int = IDLE_MIN_TOKENS) -> list[str]:
    """The chats the last run saw on a lane that has no idle compaction, over the floor."""
    if not isinstance(run, Mapping):
        return []
    lines: list[str] = []
    for chat in run.get("stranded") or []:
        if not isinstance(chat, Mapping):
            continue
        tokens = int(chat.get("tokens") or 0)
        if tokens < min_tokens:
            continue
        lines.append(t("{key}: {tokens} tokens, not compacted: lane '{lane}' has no idle compaction",
                       key=chat.get("key"), tokens=tokens, lane=chat.get("lane")))
    return lines


def _facts(run: Mapping[str, Any], seconds: float) -> str:
    """The sentence a green line is made of: when the last run was, and what it left waiting."""
    return t("Last run {age} ago, {count} chats waiting.", age=_age(seconds), count=int(run.get("waiting") or 0))


def _unasked(run: Mapping[str, Any] | None, now_ms: float) -> CheckOutcome | None:
    """What a caller that never asked the scheduler can still say: the record stopped being written.

    The record is written by the run itself, so it dates the last compaction
    independently of the job row. A caller with no scheduler to ask (`nexgen
    doctor` on its schedule, which starts no CLI) can therefore still catch a
    guard that stopped yesterday, and says nothing at all on a machine that never
    wrote one: that is a fresh install or another home, not a stopped guard.
    """
    if not isinstance(run, Mapping):
        return None
    at = float(run.get("at") or 0)
    seconds = max(0.0, (now_ms - at) / 1000.0)
    if at <= 0 or seconds <= STALE_SECONDS:
        return None
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.BROKEN,
        message=t("The context guard has written no run record for {age}: no chat has been compacted since.",
                  age=_age(seconds)),
        action=t("Run 'openclaw automations list --all' to see whether the hourly job still runs, then "
                 "'nexgen openclaw groom' by hand."),
    )


def _could_not_read(run: Mapping[str, Any] | None) -> CheckOutcome | None:
    """The failure the run wrote down when OpenClaw would not list the chats.

    A run whose listing failed now exits 0 once its alert was delivered, so the
    scheduler's row says "ok" and cannot report it. The record it leaves carries the
    error, and this is where that error reaches a person.
    """
    if not isinstance(run, Mapping):
        return None
    error = str(run.get("error") or "").strip()
    if not error:
        return None
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.BROKEN,
        message=t("The last context guard run could not read the chats, so none was compacted: {error}",
                  error=error),
        action=t("Run 'nexgen openclaw groom' and read the reason it prints."),
    )


def _missing() -> CheckOutcome:
    """No job under the engine's declaration key: nothing compacts a chat that sits idle."""
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.BROKEN,
        message=t("No hourly context guard job is registered: an idle chat grows until its next message "
                  "re-sends all of it."),
        action=t("Run 'nexgen openclaw setup' with the gateway up: it registers the hourly job "
                 "('nexgen openclaw groom')."),
    )


def _registered_within_its_window(job: Mapping[str, Any], now_ms: float) -> bool:
    """Whether the scheduler created the job less than one promised window ago."""
    created = job.get("createdAtMs")
    return isinstance(created, (int, float)) and 0 <= now_ms - float(created) <= STALE_SECONDS * 1000


def _stopped(job: Mapping[str, Any], now_ms: float) -> CheckOutcome | None:
    """The job as the scheduler holds it, or None while it is serving the chats as declared.

    Four ways a registered job stops serving: switched off, never finished a run,
    ended the last one with an error, or has not succeeded inside the window its
    own hourly cadence promises. Each one leaves every chat of every agent one cold
    prompt away from a full re-send, so each is a failure rather than a warning.
    """
    from nexgen_core.openclaw.automations import is_enabled, job_id, job_name

    name = job_name(job)
    if not is_enabled(job):
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t("The hourly context guard '{name}' is switched off: nothing compacts an idle chat.",
                      name=name),
            action=t("Run 'openclaw automations enable {job}' or 'nexgen openclaw setup'.", job=job_id(job)),
        )
    last_ms = _last_run_ms(job)
    if last_ms is None and _registered_within_its_window(job, now_ms):
        # A job registered by an install a few minutes ago has not had its first hour
        # yet: that is the cadence it promises, not a guard that stopped serving.
        return None
    if last_ms is None:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t("The hourly context guard '{name}' has never finished a run: no chat has been compacted "
                      "by it.", name=name),
            action=t("Run 'nexgen openclaw groom' once by hand to see why, then 'openclaw automations runs "
                     "{job}'.", job=job_id(job)),
        )
    status = str(job.get("lastRunStatus") or "").strip()
    if status and status != "ok":
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t("The last context guard run ended '{status}': at least one chat it was asked to compact "
                      "was not compacted.", status=status),
            action=t("Run 'openclaw automations runs {job}' to read the failure, then 'nexgen openclaw groom'.",
                     job=job_id(job)),
        )
    seconds = max(0.0, (now_ms - last_ms) / 1000.0)
    if seconds > STALE_SECONDS:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t("The last context guard run was {age} ago, more than the {hours}h the hourly job "
                      "promises: every idle chat is growing.", age=_age(seconds), hours=STALE_SECONDS // 3600),
            action=t("Run 'openclaw automations list --all' and 'nexgen openclaw groom': the job is registered "
                     "but its runs are not landing."),
        )
    return None


def check_groom(
    jobs: list[dict[str, Any]] | None,
    home: Path | None = None,
    now_ms: float | None = None,
) -> CheckOutcome | None:
    """The hourly context guard judged on the job that runs it and the record it left.

    `jobs` is what the scheduler listed, or None when nobody asked it: an unread
    scheduler says nothing about the job, because the heartbeat line already
    reports that nobody asked. A record that stopped being written is still a
    failure there, because only `nexgen openclaw groom` writes it: a guard that
    wrote one until yesterday and none since is a guard that stopped, whatever the
    scheduler would say.
    """
    from nexgen_core.openclaw.automations import context_guard_jobs

    now = now_ms if now_ms is not None else time.time() * 1000
    run = groom_run.load(home)
    if jobs is None:
        return _unasked(run, now) or _could_not_read(run)
    guard = context_guard_jobs(jobs)
    if not guard:
        return _missing()
    stopped = _stopped(guard[0], now)
    if stopped is not None:
        return stopped
    could_not_read = _could_not_read(run)
    if could_not_read is not None:
        return could_not_read
    seconds = max(0.0, (now - (_last_run_ms(guard[0]) or now)) / 1000.0)
    return _judged_run(run, groom_memory.load_memory(home), seconds)


def _judged_run(run: Mapping[str, Any] | None, memory: Mapping[str, dict[str, Any]], seconds: float) -> CheckOutcome:
    """What the last run left, once the job itself is known to be running as declared."""
    if not isinstance(run, Mapping) or float(run.get("at") or 0) <= 0:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("The context guard ran {age} ago and left no record of its queue: what it compacted and "
                      "what it left behind is unread.", age=_age(seconds)),
            action=t("Run 'nexgen openclaw groom' by hand once: it writes the record the doctor reads."),
        )
    waiting = int(run.get("waiting") or 0)
    compacted = int(run.get("compacted") or 0)
    if int(run.get("cap") or 0) <= 0:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("Grooming is switched off ({variable}=0): {count} chats wait and none is compacted.",
                      variable="NEXGEN_OPENCLAW_MAX_COMPACTIONS", count=waiting),
            action=t("Unset NEXGEN_OPENCLAW_MAX_COMPACTIONS in the gateway's environment to resume the hourly "
                     "compaction."),
        )
    problems = [*stranded_lines(run), *refused_chats(memory)]
    if waiting > 0 and compacted == 0:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t("The last context guard run left {count} chats waiting and compacted none of them.",
                      count=waiting),
            action=t("Run 'nexgen openclaw groom' and read the reason it prints for each chat."),
        )
    if problems:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("{facts} {chats}", facts=_facts(run, seconds), chats="; ".join(problems)),
            action=t("An 'acp' chat cannot be compacted by this release at all: start a fresh session to shrink "
                     "it. A chat OpenClaw refused is compacted once it is written to again."),
        )
    return CheckOutcome(id=CHECK_ID, severity=Severity.OK, message=_facts(run, seconds))
