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
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw import groom_memory

#: A context this large is compacted even while the chat is in use.
HARD_TOKENS = 600_000
#: A chat untouched for this long is compacted, whatever its size.
IDLE_SECONDS = 60 * 60
#: Session keys that belong to a run rather than to the person.
SKIP_PARTS = (":subagent:", ":cron:", ":acp:", ":harness:")
BUSY_STATES = {"running", "active", "starting"}
#: Backends that cannot prove a native compaction ran: their sessions are left alone.
UNCONFIRMABLE_RUNTIMES = frozenset({"claude-cli"})
#: A session only looked at, because a dry run changes nothing.
DRY_RUN = "dry-run"


@dataclass(frozen=True)
class Decision:
    """One session the groomer wants to compact, and why, as it looked when it was picked."""

    key: str
    agent_id: str
    tokens: int
    idle_seconds: float
    reason: str
    runtime: str
    fingerprint: dict[str, float]


@dataclass(frozen=True)
class Outcome:
    """What one compaction attempt really ended as, and the line a person reads."""

    state: str
    line: str


@dataclass(frozen=True)
class GroomReport:
    """What a run did: the lines it emitted, and how many attempts really failed."""

    lines: list[str]
    failed: int


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
        idle = _idle_seconds(entry, now_ms)
        if tokens >= HARD_TOKENS:
            reason = "over the limit"
        elif idle >= IDLE_SECONDS:
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
        ))
    return decisions


def plan_groom(
    entries: Iterable[Mapping[str, Any]], now_ms: float, memory: Mapping[str, dict[str, Any]] | None = None
) -> list[Decision]:
    """The sessions worth compacting, in the order they were listed.

    `memory` is what earlier runs attempted: a session whose counters have not
    moved since an attempt that ended well is not worth another try.
    """
    remembered = memory or {}
    return [
        decision
        for decision in _policy_decisions(entries, now_ms)
        if decision.runtime not in UNCONFIRMABLE_RUNTIMES
        and not groom_memory.settled(remembered, decision.key, decision.fingerprint)
    ]


def unconfirmable(entries: Iterable[Mapping[str, Any]], now_ms: float) -> list[Decision]:
    """The sessions the policy picks but whose backend cannot prove a compaction ran."""
    return [d for d in _policy_decisions(entries, now_ms) if d.runtime in UNCONFIRMABLE_RUNTIMES]


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
    stderr. A zero exit is the gateway's own word that it compacted, whatever
    the wording it printed, so only a non-zero one can end in failure.
    """
    shown = (proc.stdout or proc.stderr or "").strip()
    line = shown.splitlines()[-1] if shown else "no output"
    answered = f"{proc.stdout or ''}\n{proc.stderr or ''}".casefold()
    if "already compacted" in answered:
        return Outcome(groom_memory.ALREADY_COMPACTED, line)
    if "did not confirm" in answered:
        return Outcome(groom_memory.UNSUPPORTED, line)
    if proc.returncode == 0:
        return Outcome(groom_memory.COMPACTED, line)
    return Outcome(groom_memory.FAILED, line)


def compact(openclaw: str, decision: Decision, timeout: int = 300) -> Outcome:
    """Compact one session, and say what really happened."""
    try:
        proc = _run([openclaw, "sessions", "compact", decision.key, "--agent", decision.agent_id], timeout)
    except subprocess.TimeoutExpired:
        return Outcome(groom_memory.FAILED, t("timed out after {seconds}s", seconds=timeout))
    except OSError as exc:
        return Outcome(groom_memory.FAILED, str(exc))
    return _classify(proc)


def _line(decision: Decision, outcome: str) -> str:
    minutes = int(decision.idle_seconds // 60)
    return f"{decision.key}: {decision.tokens} tokens, idle {minutes}m ({decision.reason}) -> {outcome}"


def _remember_run(
    memory: dict[str, dict[str, Any]],
    entries: list[Mapping[str, Any]],
    attempts: Iterable[tuple[Decision, Outcome]],
) -> None:
    """Record this run's attempts, and forget the sessions the gateway stopped listing.

    An empty listing is not evidence: a gateway that cannot be reached answers
    with none, and erasing what is known would put every session back in the
    hourly retry loop. The first listing that actually answers does the pruning.
    """
    for decision, outcome in attempts:
        groom_memory.remember(memory, decision.key, decision.fingerprint, outcome.state)
    if entries:
        groom_memory.prune(memory, (entry.get("key") for entry in entries if entry.get("key")))


def groom(
    openclaw: str | None = None,
    now_ms: float | None = None,
    dry_run: bool = False,
    emit: Callable[[str], None] = print,
    clock: Callable[[], float] | None = None,
    home: Path | None = None,
) -> GroomReport:
    """Compact what the policy says, and report one line per session touched.

    A dry run says what it would do and remembers nothing, so being curious
    never changes what the next real run picks.
    """
    import time as _time

    binary = openclaw or _resolve_binary()
    moment = now_ms if now_ms is not None else (clock or _time.time)() * 1000
    entries = list_sessions(binary)
    memory = groom_memory.load_memory(home)
    lines: list[str] = []
    if dry_run:
        for decision in unconfirmable(entries, moment):
            lines.append(_line(decision, t("skipped: the {runtime} backend cannot confirm a native compaction",
                                           runtime=decision.runtime)))
            emit(lines[-1])
    attempts: list[tuple[Decision, Outcome]] = []
    for decision in plan_groom(entries, moment, memory=memory):
        outcome = Outcome(DRY_RUN, DRY_RUN) if dry_run else compact(binary, decision)
        attempts.append((decision, outcome))
        lines.append(_line(decision, outcome.line))
        # One line as each attempt ends: a compaction can take minutes.
        emit(lines[-1])
    if not dry_run:
        before = dict(memory)
        _remember_run(memory, entries, attempts)
        if memory != before:
            groom_memory.save_memory(memory, home)
    return GroomReport(lines=lines, failed=sum(1 for _, o in attempts if o.state == groom_memory.FAILED))
