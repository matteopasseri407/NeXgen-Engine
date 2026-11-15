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

The cap buys responsiveness from the gateway, not fairness: the queue is walked
past a chat whose attempt failed, so one chat the gateway refuses cannot starve
every other, and a failure that is not about quota waits `FAILED_BACKOFF_MS`
before it is asked again. A run that leaves chats waiting and compacts none of
them raises one alert, because the hourly job runs with `--no-deliver` and its
output otherwise reaches nobody; only the command hands in that channel, so
nothing else alerts on the policy's behalf.
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
from nexgen_core.openclaw import agy_compaction, groom_memory
from nexgen_core.openclaw.context_budget import FIRST_PROMPT_BUDGET
from nexgen_core.openclaw.groom_lines import (
    Decision,
    GroomReport,
    Outcome,
    alert_stalled,
    leftover_message,
    line,
    preview_lines,
)
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

if TYPE_CHECKING:
    from nexgen_core.megaphone import Megaphone

#: Compacting more than one session per run holds the single-threaded gateway and stalls chat requests.
MAX_COMPACTIONS_PER_RUN = 1
#: Environment variable to override the maximum number of compactions per run.
MAX_COMPACTIONS_ENV_VAR = "NEXGEN_OPENCLAW_MAX_COMPACTIONS"

#: A context this large is compacted even while the chat is in use.
HARD_TOKENS = 600_000
#: A chat untouched for this long is compacted, if it holds at least `IDLE_MIN_TOKENS`.
IDLE_SECONDS = 60 * 60
#: A chat smaller than this is left alone however long it sits idle.
#:
#: An idle compaction buys back the cold cache a chat would pay on its next message, and
#: it pays for that with a model turn over the whole conversation. Below the size of a
#: session's own first prompt (`FIRST_PROMPT_BUDGET`, measured between 77,840 and 945,389
#: on this engine) there is nothing worth buying back: the summary would cost more than
#: the cache it saves. Without the floor the hourly groom sums a summary per idle chat
#: into a bill nobody asked for, which is the churn `openclaw-quota-burn` measured.
IDLE_MIN_TOKENS = FIRST_PROMPT_BUDGET
#: Session keys that belong to a run rather than to the person.
SKIP_PARTS = (":subagent:", ":cron:", ":acp:", ":harness:")
BUSY_STATES = {"running", "active", "starting"}
#: Backends that cannot be asked for a native compaction on this machine: their sessions are left alone.
#: claude-cli is not here: OpenClaw's own backend declares a manual `/compact` and checks its output.
UNCONFIRMABLE_RUNTIMES = frozenset({"google-antigravity-cli"})
#: Harnesses whose stale token counter still counts as being over the limit, because their native compact is the
#: only way down. The idle rule does not look at this set: every chat, harness or direct model, follows it alike.
#: codex: OpenClaw refuses the manual compact while the agent has a tools deny list ("host-isolated"), and answers
#: ok with compacted=false; the memory then stops the retries until the chat is written to again.
HARNESS_RUNTIMES = frozenset({"claude-cli", "codex", "google-antigravity-cli"})
#: The reason a session is picked for size rather than for being idle.
OVER_LIMIT = "over the limit"
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


def _idle_seconds(entry: Mapping[str, Any], now_ms: float) -> float:
    stamp = entry.get("lastInteractionAt") or entry.get("updatedAt") or 0
    return max(0.0, (now_ms - float(stamp)) / 1000.0)


def _runtime_id(entry: Mapping[str, Any]) -> str:
    """Which backend runs this agent's chats, as the gateway names it."""
    runtime = entry.get("agentRuntime")
    return str(runtime.get("id") or "") if isinstance(runtime, Mapping) else ""


def _policy_decisions(entries: Iterable[Mapping[str, Any]], now_ms: float) -> list[Decision]:
    """The sessions the size and idle policy picks, before any reason to leave them alone."""
    decisions: list[Decision] = []
    for entry in entries:
        key = entry.get("key")
        agent_id = entry.get("agentId")
        if not key or not agent_id or any(part in key for part in SKIP_PARTS):
            continue
        if (entry.get("status") or "") in BUSY_STATES:
            continue
        tokens = int(entry.get("totalTokens") or 0)
        if tokens <= 0:
            continue
        # A counter the gateway marks stale is not evidence of pressure for a
        # plain chat in use: after a compaction it reports the summariser's own
        # usage. A harness is the exception: its native compact is the only way
        # down. Being idle needs no counter at all, so it applies to every chat.
        freshness = entry.get("totalTokensFresh")
        stale = freshness is False
        over_limit = tokens >= HARD_TOKENS and (not stale or _runtime_id(entry) in HARNESS_RUNTIMES)
        idle = _idle_seconds(entry, now_ms)
        if over_limit:
            reason = OVER_LIMIT
        elif idle >= IDLE_SECONDS and (stale or tokens >= IDLE_MIN_TOKENS):
            # A measured chat under `IDLE_MIN_TOKENS` costs less to leave alone than to
            # summarise; a counter nobody marked fresh cannot claim anything about size.
            reason = "idle"
        else:
            continue
        decisions.append(Decision(
            key=key,
            agent_id=agent_id,
            tokens=tokens,
            idle_seconds=idle,
            reason=reason,
            runtime=_runtime_id(entry),
            fingerprint=groom_memory.fingerprint(entry),
            provider=str(entry.get("modelProvider") or ""),
            tokens_fresh=not stale,
            # An absent field is not a fresh counter: it is the gateway not saying, and
            # the line then prints the number as a last known one instead of as a fact.
            tokens_known=isinstance(freshness, bool),
        ))
    return decisions


def plan_groom(
    entries: Iterable[Mapping[str, Any]],
    now_ms: float,
    memory: Mapping[str, dict[str, Any]] | None = None,
    blocked: frozenset[str] = UNCONFIRMABLE_RUNTIMES,
) -> list[Decision]:
    """The sessions worth compacting, in the order they were listed.

    `memory` is what earlier runs attempted: a session whose counters have not
    moved since an attempt that ended well is not worth another try, and a
    quota that has not renewed is not asked again. `blocked` is the set of
    backends this machine cannot ask to compact; the gateway host passes the one
    `blocked_runtimes` computed, a test that drives the policy alone gets the
    conservative default.
    """
    remembered = memory or {}
    return [
        decision
        for decision in _policy_decisions(entries, now_ms)
        if decision.runtime not in blocked
        and not groom_memory.settled(remembered, decision.key, decision.fingerprint)
        and not groom_memory.quota_spent(remembered, decision.key, decision.provider, now_ms)
    ]


def unconfirmable(
    entries: Iterable[Mapping[str, Any]],
    now_ms: float,
    blocked: frozenset[str] = UNCONFIRMABLE_RUNTIMES,
) -> list[Decision]:
    """The sessions the policy picks but whose backend cannot prove a compaction ran."""
    return [d for d in _policy_decisions(entries, now_ms) if d.runtime in blocked]


def blocked_runtimes(state_dir: Path | None) -> frozenset[str]:
    """The backends this machine cannot honestly ask to compact.

    `claude-cli` stays blocked. `google-antigravity-cli` is blocked only while
    the installed plugin carries no manual compaction: the engine adds one that
    runs `agy`'s own `/compact`, so once the marker is there asking is right.
    A plugin update that overwrites the file removes the marker, and the same
    check puts the backend back here instead of failing the hourly run.
    """
    if state_dir is not None and agy_compaction.patched(state_dir):
        return UNCONFIRMABLE_RUNTIMES - {LOCAL_AGY_BACKEND}
    return UNCONFIRMABLE_RUNTIMES


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
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def list_sessions(openclaw: str | None = None, timeout: int = 120) -> list[dict[str, Any]]:
    """Every stored session, across agents, as the CLI reports it."""
    binary = openclaw or _resolve_binary()
    proc = _run([binary, "sessions", "list", "--all-agents", "--json", "--limit", "all"], timeout)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    payload = json.loads(proc.stdout)
    sessions = payload.get("sessions", payload) if isinstance(payload, dict) else payload
    return list(sessions) if isinstance(sessions, list) else []


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


def _state_dir(home: Path | None) -> Path | None:
    """OpenClaw's own folder, where the installed plugins live; None when it cannot be named."""
    from nexgen_core.paths import openclaw_config

    try:
        return Path(openclaw_config(home)).parent
    except OSError:  # nexgen: allow reason="a state folder that cannot be resolved leaves every backend blocked, which is the safe answer"
        return None


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


def _groom_priority(decision: Decision) -> tuple[int, int]:
    """Sort key: sessions over the HARD threshold first, then largest token count."""
    is_hard = 1 if decision.tokens >= HARD_TOKENS or decision.reason == OVER_LIMIT else 0
    return (is_hard, decision.tokens)


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


def _switched_off_lines(planned: list[Decision], emit: Callable[[str], None]) -> list[str]:
    """What a run says when `NEXGEN_OPENCLAW_MAX_COMPACTIONS=0` switched it off."""
    if not planned:
        return []
    note = t("Grooming is switched off ({variable}=0): {count} chats were left alone.",
             variable=MAX_COMPACTIONS_ENV_VAR, count=len(planned))
    emit(note)
    return [note]


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
    """
    import time as _time

    binary = openclaw or _resolve_binary()
    moment = now_ms if now_ms is not None else (clock or _time.time)() * 1000
    entries = list_sessions(binary)
    memory = groom_memory.load_memory(home)
    blocked = blocked_runtimes(_state_dir(home))
    planned = sorted(
        plan_groom(entries, moment, memory=memory, blocked=blocked),
        key=_groom_priority, reverse=True,
    )
    limit = get_max_compactions(env)
    if dry_run:
        return GroomReport(lines=_preview(entries, planned, limit, moment, blocked, emit), failed=0)
    if limit <= 0:
        return GroomReport(lines=_switched_off_lines(planned, emit), failed=0)
    lines, attempts, deferred = _compact_queue(binary, planned, limit, moment, emit)
    if deferred:
        msg = leftover_message(len(deferred))
        lines.append(msg)
        emit(msg)
        if phone is not None and not any(outcome.state != groom_memory.FAILED for _, outcome in attempts):
            alert_stalled(len(deferred), phone)
    before = dict(memory)
    _remember_run(memory, entries, attempts, _sessions_after(binary, attempts), moment)
    if memory != before:
        groom_memory.save_memory(memory, home)
    return GroomReport(lines=lines, failed=sum(1 for _, o in attempts if o.state == groom_memory.FAILED))
