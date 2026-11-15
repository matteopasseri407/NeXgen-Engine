"""The row facts of a stored session: its lane, its size, its quiet, and the lanes nothing compacts.

One job: read what one row of `openclaw sessions list --all-agents --json` says
about a chat, and say whether `openclaw sessions compact` can act on it, so the
groomer and the doctor read one list instead of two assumptions.

Why this exists: a lane is a namespace inside a session key
(`agent:<agent>:<lane>:<id>`), and the groomer used to skip four of them
wholesale. Two are runs nobody returns to: a `:subagent:` row and a `:cron:` row
belong to one execution. The other two hold real conversations whose context the
next message re-sends, and the pinned release 2026.11.2 can compact only one of
them on demand:

- `:harness:` rows are owned by a native agent harness (codex, claude-cli,
  copilot) that declares its own compaction, and `sessions compact` reaches it:
  `isNativeHarnessCompactionSession` in the installed `dist/cli-compaction-*.mjs`
  accepts a session whose `agentHarnessId` matches its provider, and
  `compactNativeHarnessCliTranscript` then asks the harness itself to compact.
  These follow the idle rule like any other chat.
- `:acp:` rows are conversations bound to an external ACP agent. The ACP request
  surface in `@agentclientprotocol/sdk` (`schema/schema.json`) has no compact
  request at all - `session/cancel|close|delete|fork|list|load|new|prompt|
  request_permission|resume|set_config_option|set_mode|update` - and OpenClaw's
  ACP manager (`dist/manager-*.mjs`) exposes no compaction. Compacting one
  rewrites OpenClaw's transcript mirror only, while the ACP agent resumes its own
  conversation (`acp_sessions.resume_agent_session_id` in the state database), so
  the next message re-sends everything and the gateway answers `compacted: true`
  for a saving nobody gets. These are left alone, and the doctor names them
  rather than pretending they were handled.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from nexgen_core.i18n import t

#: Lanes that belong to one run rather than to a chat a person comes back to.
RUN_LANES = (":subagent:", ":cron:")
#: Lanes whose runtime owns native compaction, so `sessions compact` asks the harness itself.
COMPACT_ON_DEMAND = (":harness:",)
#: Lanes nothing can compact on demand on this release: their context belongs to an external agent.
NO_IDLE_COMPACTION = (":acp:",)
#: The parts of a key that put it outside the idle rule: the runs, and the lane that cannot answer.
SKIP_PARTS = RUN_LANES + NO_IDLE_COMPACTION
#: The names of the lanes that cannot be compacted, as a report prints them.
STRANDED_LANES = frozenset(part.strip(":") for part in NO_IDLE_COMPACTION)
#: Session states in which a chat is being answered and must be left to finish.
BUSY_STATES = frozenset({"running", "active", "starting"})


@dataclass(frozen=True)
class LaneChat:
    """One chat sitting on a lane nobody can compact, with the size it is left at."""

    key: str
    lane: str
    tokens: int


def idle_seconds(entry: Mapping[str, Any], now_ms: float) -> float:
    """How long this chat has been untouched, by the last date the gateway reported."""
    stamp = entry.get("lastInteractionAt") or entry.get("updatedAt") or 0
    return max(0.0, (now_ms - float(stamp)) / 1000.0)


def lane_name(key: str) -> str | None:
    """The lane this key belongs to, or None when it is an ordinary agent chat."""
    for part in (*COMPACT_ON_DEMAND, *NO_IDLE_COMPACTION):
        if part in key:
            return part.strip(":")
    return None


def stranded_chats(
    entries: Iterable[Mapping[str, Any]],
    now_ms: float,
    *,
    min_tokens: int,
    quiet_seconds: float,
) -> list[LaneChat]:
    """The chats on a lane with no idle compaction that are worth naming, smallest first.

    The rule is the policy's own: a chat is worth naming when it has been quiet
    past `quiet_seconds` and holds at least `min_tokens`, or cannot prove it holds
    less. A busy chat is left to finish, exactly as the policy leaves it.
    """
    found: list[LaneChat] = []
    for entry in entries:
        key = str(entry.get("key") or "")
        lane = lane_name(key)
        if lane not in STRANDED_LANES or not entry.get("agentId"):
            continue
        if (entry.get("status") or "") in BUSY_STATES:
            continue
        tokens = int(entry.get("totalTokens") or 0)
        stale = entry.get("totalTokensFresh") is False
        if tokens <= 0 or idle_seconds(entry, now_ms) < quiet_seconds:
            continue
        if not stale and tokens < min_tokens:
            continue
        found.append(LaneChat(key=key, lane=lane, tokens=tokens))
    return sorted(found, key=lambda chat: chat.tokens)


def stranded_message(chats: Iterable[LaneChat]) -> str:
    """The one line a run prints about the chats no command on this release can compact."""
    named = list(chats)
    keys = ", ".join(chat.key for chat in named)
    return t("{count} chat(s) on a lane this release cannot compact: {keys}", count=len(named), keys=keys)
