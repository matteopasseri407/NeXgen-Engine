"""Keep every agent chat small enough that a cold cache cannot cost a fortune.

One job: decide which agent conversations to compact, then compact them. The
decision is a pure function so it can be tested without a gateway; the action
shells out to the OpenClaw CLI, which is the only supported writer.

Why this exists: a session left idle at 900k tokens pays a full prompt-cache
rewrite on the next message. The main chat is never archived or deleted here:
it stays alive and small until the person clears it by hand.

An attempt is remembered (`groom_memory`) so a session that cannot get smaller
is not retried every hour, and a backend that cannot confirm a native
compaction is never asked: both are what issue #148 was.

The idle rule is a cache argument, and a cache argument has a floor: below
`IDLE_MIN_TOKENS` the compaction costs more than the cold cache it prevents, so a
chat that sat quiet for an hour is only compacted when it is actually large. That
floor is what stops an hourly model bill for a bot nobody is talking to.

Which model pays for the summary is not this module's to choose: OpenClaw runs
compaction on `agents.defaults.compaction.model` when it is set (the key `squad`
writes through `runtime.compaction_model`, and `background` writes when the
default is local) and on the session's own model otherwise. `openclaw sessions
compact` takes a key, an agent, `--max-lines` and a timeout, and no model: the
pinned 2026.11.2 answers `--help` with exactly those options, so the cheapest
configured model is chosen where the key is written, not here.

Which lanes are picked is `sessions_rows`' question and the reason it exists: a
`:subagent:` or `:cron:` row is a run nobody returns to, a `:harness:` row is a
chat whose runtime owns native compaction and is therefore compacted like any
other, and a `:acp:` row is a conversation bound to an external agent that cannot
be asked to compact at all, so it is left alone and named by the doctor.

The cap buys responsiveness from the gateway, not fairness: the queue is walked
past a chat whose attempt failed, so one chat the gateway refuses cannot starve
every other, and a failure that is not about quota waits `FAILED_BACKOFF_MS`
before it is asked again. A refused chat never fails the run: the scheduler
switches a job off after ten failed runs, and one chat must not cost every other
its guard. A run fails only when the sessions cannot be listed: that failure is
written down (`groom_run`) and the command raises an alert, then exits 0 when the
alert was delivered, so the scheduler does not switch the job off for a gateway
that was briefly unreachable. A run that gets no due chat through raises one alert,
because the hourly job runs with `--no-deliver` and its output otherwise reaches
nobody; only the command hands in that channel, so nothing else alerts on the
policy's behalf. What the run did is also written down (`groom_run`) for the
doctor, which cannot ask the scheduler for it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexgen_core.i18n import t
from nexgen_core.openclaw import groom_memory, groom_run, sessions_rows
from nexgen_core.openclaw.groom_lines import (
    Decision,
    GroomReport,
    Outcome,
    alert_stalled,
    leftover_message,
    line,
    preview_lines,
)
from nexgen_core.openclaw.groom_policy import IDLE_MIN_TOKENS, IDLE_SECONDS, plan_groom, priority, unconfirmable
from nexgen_core.openclaw.node_cli import launch_argv
from nexgen_core.openclaw.sessions_archived import UnreadableSessions, drop_archived, read_listing
from nexgen_core.openclaw.sessions_blocked import askable, state_dir_of

if TYPE_CHECKING:
    from nexgen_core.megaphone import Megaphone

#: Compacting more than one session per run holds the single-threaded gateway and stalls chat requests.
MAX_COMPACTIONS_PER_RUN = 1
#: Environment variable to override the maximum number of compactions per run.
MAX_COMPACTIONS_ENV_VAR = "NEXGEN_OPENCLAW_MAX_COMPACTIONS"

#: The renewal the provider states when a quota is spent; asking before it only earns another rejection.
_RETRY_AFTER = re.compile(r"retry-after:\s*(\d+)", re.IGNORECASE)
_SPENT_QUOTA = re.compile(
    r"\b429\b|usage.?limit|quota|rate.?limit|hit your limit|resource.?exhausted"
    r"|credit balance|insufficient.{0,12}credit|out of credits",
    re.IGNORECASE,
)
#: How long to leave a spent quota alone when the provider does not say when it renews.
UNDATED_QUOTA_WAIT_MS = 6 * 60 * 60 * 1000
#: How long a chat whose attempt failed for another reason is left alone.
#:
#: Six hours, the same window as an undated quota: asking again the next hour is what
#: let one chat the gateway refuses eat a whole run, run after run.
FAILED_BACKOFF_MS = 6 * 60 * 60 * 1000


def _resolve_binary(name: str = "openclaw") -> str:
    """The executable, never a directory that happens to share the name.

    `subprocess` resolves a bare name through PATH on its own, and a PATH entry
    like an empty segment points at the current directory. On a gateway host
    that directory contains `~/.openclaw/openclaw`, so a bare name fails with
    EACCES instead of running the CLI. Resolve it here, once.
    """
    import os
    import shutil

    found = shutil.which(name)
    if found and os.path.isfile(found) and os.access(found, os.X_OK):
        return found
    for candidate in (os.path.expanduser(f"~/.npm-global/bin/{name}"), f"/usr/local/bin/{name}", f"/usr/bin/{name}"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(f"the {name} CLI is not on PATH and not in the usual places")


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    # Windows' npm shim is a batch file, and cmd.exe re-reads the quotes of a JSON argument.
    if args[0].lower().endswith(".cmd"):
        args = launch_argv(args) or args
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def list_sessions(openclaw: str | None = None, timeout: int = 120) -> list[dict[str, Any]]:
    """Every stored session, across agents, as the CLI reports it.

    A listing that fails raises: read as empty it would say "nothing to compact" for ever.
    """
    binary = openclaw or _resolve_binary()
    proc = _run([binary, "sessions", "list", "--all-agents", "--json", "--limit", "all"], timeout)
    return list(read_listing(proc, "sessions list")["sessions"])


def _classify(proc: subprocess.CompletedProcess[str]) -> Outcome:
    """Name what the gateway's answer means: done, pointless, unprovable, or a real failure.

    Both streams are searched: a warning on stdout must not hide the reason on
    stderr. The gateway's JSON answer (`--json`) says whether it compacted in its
    `compacted` field; that field alone is success. An exit code, or text that
    is not that answer, never proves a compaction. A backend that owns
    compaction but offers no manual one refuses every hour the same way, so its
    answer is unprovable rather than failed.
    """
    shown = (proc.stdout or proc.stderr or "").strip()
    line = shown.splitlines()[-1] if shown else "no output"
    answered = f"{proc.stdout or ''}\n{proc.stderr or ''}".casefold()
    if "already compacted" in answered:
        return Outcome(groom_memory.ALREADY_COMPACTED, line)
    if "did not confirm" in answered or "does not support manual compaction" in answered:
        return Outcome(groom_memory.UNSUPPORTED, line)
    payload = _json_answer(proc.stdout)
    if payload is None or not isinstance(payload.get("compacted"), bool):
        # Not the gateway's JSON answer: whatever the exit code, nothing is claimed as compacted.
        return Outcome(groom_memory.FAILED, line if payload is None else "no compacted field in the answer")
    detail = str(payload.get("reason") or f"compacted={payload['compacted']}")
    if payload.get("ok") is not True:
        return Outcome(groom_memory.FAILED, detail)
    if payload["compacted"]:
        return Outcome(groom_memory.COMPACTED, detail)
    return Outcome(groom_memory.NOT_COMPACTED, detail)


def _json_answer(stdout: str | None) -> dict[str, Any] | None:
    """The gateway's JSON answer, or None when the text is not a JSON object."""
    try:
        payload = json.loads(stdout or "")
    except ValueError:  # nexgen: allow reason="a non-JSON answer is reported as unconfirmed, never as success"
        return None
    return payload if isinstance(payload, dict) else None


def compact(openclaw: str, decision: Decision, timeout: int = 300) -> Outcome:
    """Compact one session, and say what really happened."""
    try:
        proc = _run(
            [openclaw, "sessions", "compact", decision.key, "--agent", decision.agent_id, "--json"], timeout
        )
    except subprocess.TimeoutExpired:
        return Outcome(groom_memory.FAILED, t("timed out after {seconds}s", seconds=timeout))
    except OSError as exc:
        return Outcome(groom_memory.FAILED, str(exc))
    return _classify(proc)


def _sessions_after(binary: str, attempts: list[tuple[Decision, Outcome]]) -> dict[str, Mapping[str, Any]]:
    """The attempted sessions as they look now; empty when there were none or the gateway cannot say."""
    if not attempts:
        return {}
    try:
        return {str(entry["key"]): entry for entry in list_sessions(binary) if entry.get("key")}
    except (subprocess.TimeoutExpired, OSError, ValueError):  # nexgen: allow reason="without a fresh listing the counters seen before the attempt are remembered"
        return {}


def _quota_renews_at(outcome: Outcome, now_ms: float) -> float | None:
    """When a failure caused by a spent quota can be tried again; None when it was not about quota."""
    if outcome.state != groom_memory.FAILED:
        return None
    stated = _RETRY_AFTER.search(outcome.line)
    if stated:
        return now_ms + int(stated.group(1)) * 1000
    return now_ms + UNDATED_QUOTA_WAIT_MS if _SPENT_QUOTA.search(outcome.line) else None


def _remember_run(
    memory: dict[str, dict[str, Any]],
    entries: list[Mapping[str, Any]],
    attempts: Iterable[tuple[Decision, Outcome]],
    after: Mapping[str, Mapping[str, Any]],
    now_ms: float,
) -> None:
    """Record this run's attempts, and forget the sessions the gateway stopped listing.

    An attempt is remembered with the counters the session has after it: a
    compaction itself moves a chat's date and token count, and the counters seen
    before it would look like new activity an hour later, on a chat nobody touched.

    An empty listing is not evidence: a gateway that cannot be reached answers
    with none, and erasing what is known would put every session back in the
    hourly retry loop. The first listing that actually answers does the pruning.
    """
    for decision, outcome in attempts:
        current = after.get(decision.key)
        mark = groom_memory.fingerprint(current) if current else decision.fingerprint
        renews = _quota_renews_at(outcome, now_ms)
        provider = decision.provider
        if renews is None and outcome.state == groom_memory.FAILED:
            # A failure that says nothing about quota is not asked again next hour: one
            # chat the gateway refuses would otherwise eat every run. The wait is per
            # chat, not per provider, so a broken chat does not hold its siblings back.
            renews, provider = now_ms + FAILED_BACKOFF_MS, ""
        groom_memory.remember(
            memory, decision.key, mark, outcome.state, retry_at=renews, provider=provider,
            runtime=decision.runtime,
        )
    if entries:
        groom_memory.prune(memory, (entry.get("key") for entry in entries if entry.get("key")))


def get_max_compactions(env: Mapping[str, str] | None = None) -> int:
    """The compaction cap in force: environment variable when set and valid, default otherwise.

    Zero is a value, not a mistake: `NEXGEN_OPENCLAW_MAX_COMPACTIONS=0` switches the
    groom off, which is how a person stops an hourly compaction bill without editing the
    cron job. Anything that is not a count falls back to the default.
    """
    source = env if env is not None else os.environ
    raw = source.get(MAX_COMPACTIONS_ENV_VAR, "").strip()
    if raw.isdigit():
        return int(raw)
    return MAX_COMPACTIONS_PER_RUN


def _compact_queue(
    binary: str,
    planned: list[Decision],
    limit: int,
    moment: float,
    emit: Callable[[str], None],
) -> tuple[list[str], list[tuple[Decision, Outcome]], list[Decision]]:
    """Walk the planned queue, and say what was attempted and what waits.

    The cap is spent on outcomes, not on attempts: a chat whose attempt failed does
    not take the turn away from the next one, so a chat the gateway refuses every
    hour cannot starve the others. Failures are still bounded to one over the cap,
    so a gateway refusing everything cannot hold the hourly job for the whole queue
    either. A chat whose provider has just spent a quota in this run waits for the
    next run instead of being asked and refused again.
    """
    lines: list[str] = []
    attempts: list[tuple[Decision, Outcome]] = []
    deferred: list[Decision] = []
    spent: set[str] = set()
    settled = failures = 0
    for decision in planned:
        if settled >= limit or failures > limit or decision.provider in spent:
            deferred.append(decision)
            continue
        outcome = compact(binary, decision)
        if decision.provider and _quota_renews_at(outcome, moment) is not None:
            spent.add(decision.provider)
        attempts.append((decision, outcome))
        lines.append(line(decision, outcome.line))
        # One line as each attempt ends: a compaction can take minutes.
        emit(lines[-1])
        if outcome.state == groom_memory.FAILED:
            failures += 1
        else:
            settled += 1
    return lines, attempts, deferred


def _failures(
    attempts: list[tuple[Decision, Outcome]], deferred: list[Decision], phone: Megaphone | None
) -> int:
    """How many attempts failed; says so aloud when chats were due and none got through.

    A refusal does not fail the job, so this alert is how it is heard.
    """
    failed = sum(1 for _, outcome in attempts if outcome.state == groom_memory.FAILED)
    if phone is not None and failed == len(attempts) and (failed or deferred):
        alert_stalled(failed + len(deferred), phone)
    return failed


def _preview(
    entries: list[dict[str, Any]],
    planned: list[Decision],
    limit: int,
    moment: float,
    blocked: frozenset[str],
    emit: Callable[[str], None],
) -> list[str]:
    """The whole queue a run would pick from, preceded by the backends it cannot ask."""
    lines: list[str] = []
    for decision in unconfirmable(entries, moment, blocked):
        lines.append(line(decision, t("skipped: the {runtime} backend cannot confirm a native compaction",
                                      runtime=decision.runtime)))
        emit(lines[-1])
    lines.extend(preview_lines(planned, limit, emit))
    return lines


def _decide(
    entries: list[dict[str, Any]],
    moment: float,
    memory: dict[str, dict[str, Any]],
    state_dir: Path | None,
    env: Mapping[str, str] | None,
    dry_run: bool,
    emit: Callable[[str], None],
) -> tuple[list[Decision], int, frozenset[str], list[sessions_rows.LaneChat]]:
    """What this run will do: the queue, the cap, the backends it may ask, and the chats nothing can shrink.

    Nothing here touches the gateway: the memory is read, a plugin fix is put back
    unless this is a dry run, and the policy is asked. The chats on a lane with no
    idle compaction are said once, because a person who reads "nothing to do" must
    not read it over a chat this release has no way to shrink.
    """
    blocked = askable(state_dir, memory, dry_run=dry_run, emit=emit)
    planned = sorted(
        plan_groom(entries, moment, memory=memory, blocked=blocked),
        key=priority, reverse=True,
    )
    stranded = sessions_rows.stranded_chats(entries, moment, min_tokens=IDLE_MIN_TOKENS,
                                            quiet_seconds=IDLE_SECONDS)
    if stranded:
        emit(sessions_rows.stranded_message(stranded))
    return planned, get_max_compactions(env), blocked, stranded


def _switched_off_lines(planned: list[Decision], emit: Callable[[str], None]) -> list[str]:
    """What a run says when `NEXGEN_OPENCLAW_MAX_COMPACTIONS=0` switched it off."""
    if not planned:
        return []
    note = t("Grooming is switched off ({variable}=0): {count} chats were left alone.",
             variable=MAX_COMPACTIONS_ENV_VAR, count=len(planned))
    emit(note)
    return [note]


def _read_sessions(binary: str, home: Path | None, moment: float, dry_run: bool) -> list[dict[str, Any]]:
    """Every stored session without the archived ones, or a recorded failure.

    An archived session is not a chat: 2026.11.10 lists it without saying so and
    refuses to compact it. A listing that cannot be read is recorded before it is
    raised, because the command turns it into an alert and a zero exit and the
    scheduler's own row can then no longer say what went wrong.
    """
    try:
        return drop_archived(list_sessions(binary), binary, _run)
    except UnreadableSessions as exc:
        if not dry_run:
            groom_run.save(groom_run.failure(at_ms=moment, error=str(exc)), home)
        raise


def groom(
    openclaw: str | None = None,
    now_ms: float | None = None,
    dry_run: bool = False,
    emit: Callable[[str], None] = print,
    clock: Callable[[], float] | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    phone: Megaphone | None = None,
) -> GroomReport:
    """Compact what the policy says, and report one line per session touched.

    A dry run says what it would do and remembers nothing, so being curious
    never changes what the next real run picks. `phone` is the alert channel a
    stalled queue is raised through: the command hands one in, and a caller that
    drives the policy alone gets silence rather than an alert nobody asked for.

    Before deciding, the run puts back the one thing a plugin update removes: the
    `agy` backend's manual compaction. `agent-sync` only aligns the OpenClaw
    release, so without this the first update after a setup silently stopped
    every antigravity chat from being compacted until a person ran the setup again.
    The chats that backend was refused for are offered again the moment it can
    answer, because the memory of a machine that could not ask proves nothing.
    """
    import time as _time

    binary = openclaw or _resolve_binary()
    moment = now_ms if now_ms is not None else (clock or _time.time)() * 1000
    state_dir = state_dir_of(home)
    entries = _read_sessions(binary, home, moment, dry_run)
    memory = groom_memory.load_memory(home)
    # Taken before the plugin fix, because forgetting what a repaired backend was
    # refused for is a change this run has to save, not one to compare away.
    before = dict(memory)
    planned, limit, blocked, stranded = _decide(
        entries, moment, memory, state_dir, env, dry_run, emit,
    )
    if dry_run:
        return GroomReport(lines=_preview(entries, planned, limit, moment, blocked, emit), failed=0)
    if limit <= 0:
        # A switched-off run still publishes what it forgot: a repaired backend must
        # not stay refused when the person switches compaction back on.
        groom_memory.save_changed(memory, before, home)
        groom_run.save(groom_run.record(
            at_ms=moment, cap=limit, waiting=len(planned), attempts=[], stranded=stranded,
        ), home)
        return GroomReport(lines=_switched_off_lines(planned, emit), failed=0)
    lines, attempts, deferred = _compact_queue(binary, planned, limit, moment, emit)
    if deferred:
        msg = leftover_message(len(deferred))
        lines.append(msg)
        emit(msg)
    failed = _failures(attempts, deferred, phone)
    _remember_run(memory, entries, attempts, _sessions_after(binary, attempts), moment)
    groom_memory.save_changed(memory, before, home)
    groom_run.save(groom_run.record(
        at_ms=moment, cap=limit, waiting=len(deferred), attempts=attempts, stranded=stranded,
    ), home)
    return GroomReport(lines=lines, failed=failed)
