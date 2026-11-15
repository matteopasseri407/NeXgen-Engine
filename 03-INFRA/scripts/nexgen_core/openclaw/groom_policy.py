"""Which agent chats a groom run picks, and why: the size and idle rule, as one pure decision.

One job: turn the session rows the gateway lists, the memory of earlier attempts
and the backends this machine cannot ask into the queue a run would take, largest
first. Nothing here reads a file, starts a process or writes anything.

Why it exists: the rule is the part worth arguing about, and it belongs where it
can be tested without a gateway. The rules themselves are the reason the module
carries a whole page of comments:

- a chat untouched for `IDLE_SECONDS` and holding at least `IDLE_MIN_TOKENS` is
  compacted, because an idle compaction buys back the cold cache its next message
  would pay for, and below the floor the summary costs more than the cache;
- a chat over `HARD_TOKENS` is compacted even while in use, because its next turn
  is already the expensive one;
- a counter the gateway marks stale is not evidence of pressure for a plain chat
  in use (after a compaction it reports the summariser's own usage), but a harness
  is the exception, because its native compact is the only way down.

An attempt already made with the counters a chat has now is not repeated
(`groom_memory.settled`), and a provider whose quota is spent is not asked again
before it renews: issue #148 was exactly the hourly retry of both.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from nexgen_core.openclaw import groom_memory
from nexgen_core.openclaw.context_budget import FIRST_PROMPT_BUDGET
from nexgen_core.openclaw.groom_lines import Decision
from nexgen_core.openclaw.sessions_blocked import UNCONFIRMABLE_RUNTIMES
from nexgen_core.openclaw.sessions_rows import BUSY_STATES, SKIP_PARTS, idle_seconds

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
#: Harnesses whose stale token counter still counts as being over the limit, because their native compact is the
#: only way down. The idle rule does not look at this set: every chat, harness or direct model, follows it alike.
#: codex: OpenClaw refuses the manual compact while the agent has a tools deny list ("host-isolated"), and answers
#: ok with compacted=false; the memory then stops the retries until the chat is written to again.
HARNESS_RUNTIMES = frozenset({"claude-cli", "codex", "google-antigravity-cli"})
#: The reason a session is picked for size rather than for being idle.
OVER_LIMIT = "over the limit"


def _runtime_id(entry: Mapping[str, Any]) -> str:
    """Which backend runs this agent's chats, as the gateway names it."""
    runtime = entry.get("agentRuntime")
    return str(runtime.get("id") or "") if isinstance(runtime, Mapping) else ""


def policy_decisions(entries: Iterable[Mapping[str, Any]], now_ms: float) -> list[Decision]:
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
        idle = idle_seconds(entry, now_ms)
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
        for decision in policy_decisions(entries, now_ms)
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
    return [d for d in policy_decisions(entries, now_ms) if d.runtime in blocked]


def priority(decision: Decision) -> tuple[int, int]:
    """Sort key: sessions over the HARD threshold first, then largest token count."""
    is_hard = 1 if decision.tokens >= HARD_TOKENS or decision.reason == OVER_LIMIT else 0
    return (is_hard, decision.tokens)
