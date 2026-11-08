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
from nexgen_core.openclaw import agy_compaction, groom_memory
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

#: A context this large is compacted even while the chat is in use.
HARD_TOKENS = 600_000
#: A chat untouched for this long is compacted, whatever its size.
IDLE_SECONDS = 60 * 60
#: Session keys that belong to a run rather than to the person.
SKIP_PARTS = (":subagent:", ":cron:", ":acp:", ":harness:")
BUSY_STATES = {"running", "active", "starting"}
#: Backends that cannot be asked for a native compaction on this machine: their sessions are left alone.
#: claude-cli is not here: OpenClaw's own backend declares a manual `/compact` and checks its output.
UNCONFIRMABLE_RUNTIMES = frozenset({"google-antigravity-cli"})
#: Harness CLIs that compact natively. Their sessions are compacted only when over the limit, never for being idle.
HARNESS_RUNTIMES = frozenset({"claude-cli", "google-antigravity-cli"})
#: A session only looked at, because a dry run changes nothing.
DRY_RUN = "dry-run"
#: The reason a session is picked for size rather than for being idle.
OVER_LIMIT = "over the limit"


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
        # A counter the gateway marks stale is not evidence of pressure for a
        # plain chat: after a compaction it reports the summariser's own usage.
        # A harness CLI over the limit is the exception: its native compact is
        # the only way down, and the memory stops retries while the counters
        # stay put. Under the limit, a stale harness counter is still ignored.
        stale = entry.get("totalTokensFresh") is False
        if stale and not (_runtime_id(entry) in HARNESS_RUNTIMES and tokens >= HARD_TOKENS):
            continue
        if tokens <= 0:
            continue
        idle = _idle_seconds(entry, now_ms)
        if tokens >= HARD_TOKENS:
            reason = OVER_LIMIT
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
    entries: Iterable[Mapping[str, Any]],
    now_ms: float,
    memory: Mapping[str, dict[str, Any]] | None = None,
    blocked: frozenset[str] = UNCONFIRMABLE_RUNTIMES,
) -> list[Decision]:
    """The sessions worth compacting, in the order they were listed.

    `memory` is what earlier runs attempted: a session whose counters have not
    moved since an attempt that ended well is not worth another try. `blocked`
    is the set of backends this machine cannot ask to compact; the gateway host
    passes the one `blocked_runtimes` computed, a test that drives the policy
    alone gets the conservative default.
    """
    remembered = memory or {}
    return [
        decision
        for decision in _policy_decisions(entries, now_ms)
        if decision.runtime not in blocked
        and not (decision.runtime in HARNESS_RUNTIMES and decision.reason != OVER_LIMIT)
        and not groom_memory.settled(remembered, decision.key, decision.fingerprint)
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
    blocked = blocked_runtimes(_state_dir(home))
    lines: list[str] = []
    if dry_run:
        for decision in unconfirmable(entries, moment, blocked):
            lines.append(_line(decision, t("skipped: the {runtime} backend cannot confirm a native compaction",
                                           runtime=decision.runtime)))
            emit(lines[-1])
    attempts: list[tuple[Decision, Outcome]] = []
    for decision in plan_groom(entries, moment, memory=memory, blocked=blocked):
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
