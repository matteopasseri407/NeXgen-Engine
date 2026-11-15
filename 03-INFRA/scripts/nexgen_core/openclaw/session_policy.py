"""How long a conversation may sit before OpenClaw starts a fresh one.

One job: decide the `session.reset` policy the engine proposes, and say why it
proposes one at all.

OpenClaw's own default is `reset.mode: none`: a conversation is bounded only by
compaction, and every compaction is a model call over the whole context. A bot
that answers on a channel therefore grows one conversation forever, and silence
is what makes it expensive. An idle reset costs no model call: the conversation
is dropped and the next message starts from the agent's memory files, which is
where the durable half of a chat already lives.

It lives here rather than in `profile.py` because it is a different decision from
the roster and the models, and because the composer there is already at its size
ceiling.
"""
from __future__ import annotations

from typing import Any

#: How long one channel conversation may sit untouched before it starts fresh: one
#: day. A conversation untouched for a day is a new topic to the person writing to
#: it, and a shorter horizon would cut a thread someone put down for an evening.
CHANNEL_RESET_IDLE_MINUTES = 24 * 60

#: The keys a person writes when they want their own reset policy, any one of which
#: makes the whole question theirs.
_PERSONAL = ("reset", "resetByType", "resetByChannel")


def channels_in_force(current: dict[str, Any] | None) -> list[str]:
    """The channels this machine answers on, in either shape the file stores them."""
    channels = current.get("channels") if isinstance(current, dict) else None
    if isinstance(channels, dict):
        return [str(name) for name, block in channels.items()
                if not isinstance(block, dict) or block.get("enabled") is not False]
    if isinstance(channels, list):
        return [str(name) for name in channels if name]
    return []


def session_policy(current: dict[str, Any] | None) -> dict[str, Any]:
    """The `session` section to propose, or nothing.

    Only a machine that answers on a channel gets this: a machine with no channel is
    one person talking to their own assistant, where the engine has no evidence of the
    cost it would be spending. A reset policy already in the file is the person's, at
    any of the three keys OpenClaw reads. OpenClaw documents that a background write
    (a cron wakeup, an automation job) updates a session without renewing an idle
    reset, so the unattended jobs cannot hold a conversation open by themselves.
    """
    if not channels_in_force(current):
        return {}
    session = current.get("session") if isinstance(current, dict) else None
    block = session if isinstance(session, dict) else {}
    if any(isinstance(block.get(key), dict) and block[key] for key in _PERSONAL):
        return {}
    return {"reset": {"mode": "idle", "idleMinutes": CHANNEL_RESET_IDLE_MINUTES}}
