"""What one groom run says, and the one alert it raises.

One job: turn a groom decision and its outcome into the line a person reads, say
how many eligible chats were left for later, and raise the alert of a queue that
stopped moving. The policy and the gateway call live in `sessions_groom`; what a
run says is here, so the sentences can be checked without a gateway.

Why this exists: the hourly job runs with `--no-deliver`, so these lines reach
nobody unless a person types `nexgen openclaw groom`. A run that leaves chats
waiting while compacting none of them is the one case worth interrupting for, and
it goes out through the megaphone, which debounces it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nexgen_core.i18n import t

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from nexgen_core.megaphone import Megaphone

#: A session only looked at, because a dry run changes nothing.
DRY_RUN = "dry-run"
#: The debounce key of the alert a stalled queue raises: one per install, not per chat.
STALLED_ALERT_KEY = "openclaw:groom:stalled"


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
    provider: str = ""
    tokens_fresh: bool = True
    tokens_known: bool = True


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


def line(decision: Decision, outcome: str) -> str:
    """The one line a session's attempt is reported as.

    A counter the gateway marked stale, and a counter it never judged at all, are
    both printed as a last known number: only a measured one is stated as a fact.
    """
    minutes = int(decision.idle_seconds // 60)
    if not decision.tokens_known:
        size = f"last known {decision.tokens} tokens, counter unknown"
    elif decision.tokens_fresh:
        size = f"{decision.tokens} tokens"
    else:
        size = f"last known {decision.tokens} tokens, counter stale"
    return f"{decision.key}: {size}, idle {minutes}m ({decision.reason}) -> {outcome}"


def leftover_message(count: int) -> str:
    """The one sentence that says how many eligible chats this run left alone."""
    if count == 1:
        return t("1 eligible session left for later")
    return t("{count} eligible sessions left for later", count=count)


def alert_stalled(count: int, phone: Megaphone) -> bool:
    """Tell the person that the groom left chats waiting and compacted none of them.

    The hourly job runs with `--no-deliver`, so nothing a run prints reaches a person:
    a queue that stopped moving would otherwise be silent until somebody looked. The
    megaphone debounces the key, so a gateway refusing every compaction says so once
    per window instead of once per hour.
    """
    return phone.send_alert(
        title=t("The context guard is not compacting"),
        message=t("The last run compacted none of the chats waiting for it. Waiting now: {count}.", count=count),
        action=t("Run 'nexgen openclaw groom' and read the reason it prints."),
        alert_key=STALLED_ALERT_KEY,
    )


def preview_lines(
    decisions: Iterable[Decision],
    limit: int,
    emit: Callable[[str], None],
) -> list[str]:
    """The whole queue a real run would pick from, and how many it would take.

    A preview is not a dry attempt: nothing was compacted and nothing was deferred, so
    the queue is what there is to see, and the note says how many a real run takes.
    """
    lines: list[str] = []
    for decision in decisions:
        lines.append(line(decision, DRY_RUN))
        emit(lines[-1])
    if lines:
        note = t("A real run compacts at most {limit} of these, largest first.", limit=limit)
        lines.append(note)
        emit(note)
    return lines
