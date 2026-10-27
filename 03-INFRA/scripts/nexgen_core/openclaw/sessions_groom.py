"""Keep every agent chat small enough that a cold cache cannot cost a fortune.

One job: decide which agent conversations to compact, then compact them. The
decision is a pure function so it can be tested without a gateway; the action
shells out to the OpenClaw CLI, which is the only supported writer.

Why this exists: a session left idle at 900k tokens pays a full prompt-cache
rewrite on the next message. The main chat is never archived or deleted here:
it stays alive and small until the person clears it by hand.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: A context this large is compacted even while the chat is in use.
HARD_TOKENS = 600_000
#: A chat untouched for this long is compacted, whatever its size.
IDLE_SECONDS = 60 * 60
#: Session keys that belong to a run rather than to the person.
SKIP_PARTS = (":subagent:", ":cron:", ":acp:", ":harness:")
BUSY_STATES = {"running", "active", "starting"}


@dataclass(frozen=True)
class Decision:
    """One session the groomer wants to compact, and why."""

    key: str
    agent_id: str
    tokens: int
    idle_seconds: float
    reason: str


def _idle_seconds(entry: Mapping[str, Any], now_ms: float) -> float:
    stamp = entry.get("lastInteractionAt") or entry.get("updatedAt") or 0
    return max(0.0, (now_ms - float(stamp)) / 1000.0)


def plan_groom(entries: Iterable[Mapping[str, Any]], now_ms: float) -> list[Decision]:
    """The sessions worth compacting, in the order they were listed."""
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
        decisions.append(Decision(key=key, agent_id=agent_id, tokens=tokens, idle_seconds=idle, reason=reason))
    return decisions


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def list_sessions(openclaw: str = "openclaw", timeout: int = 120) -> list[dict[str, Any]]:
    """Every stored session, across agents, as the CLI reports it."""
    proc = _run([openclaw, "sessions", "list", "--all-agents", "--json", "--limit", "all"], timeout)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    payload = json.loads(proc.stdout)
    sessions = payload.get("sessions", payload) if isinstance(payload, dict) else payload
    return list(sessions) if isinstance(sessions, list) else []


def compact(openclaw: str, decision: Decision, timeout: int = 300) -> str:
    """Compact one session, and say in a line what happened."""
    proc = _run([openclaw, "sessions", "compact", decision.key, "--agent", decision.agent_id], timeout)
    if proc.returncode == 0 and "Compacted" in (proc.stdout or ""):
        return (proc.stdout or "").strip().splitlines()[-1]
    output = (proc.stdout or proc.stderr or "").strip()
    return output.splitlines()[-1] if output else "no output"


def groom(
    openclaw: str = "openclaw",
    now_ms: float | None = None,
    dry_run: bool = False,
    emit: Callable[[str], None] = print,
    clock: Callable[[], float] | None = None,
) -> list[str]:
    """Compact what the policy says, and return one line per session touched."""
    import time as _time

    moment = now_ms if now_ms is not None else (clock or _time.time)() * 1000
    lines: list[str] = []
    for decision in plan_groom(list_sessions(openclaw), moment):
        minutes = int(decision.idle_seconds // 60)
        if dry_run:
            outcome = "dry-run"
        else:
            outcome = compact(openclaw, decision)
        line = f"{decision.key}: {decision.tokens} tokens, idle {minutes}m ({decision.reason}) -> {outcome}"
        lines.append(line)
        emit(line)
    return lines
