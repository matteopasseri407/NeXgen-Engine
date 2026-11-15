"""The bindings a configured channel needs before it can reach an agent.

One job: name the channel bindings OpenClaw is missing, and nothing else.

With `agents.ownership: "explicit"` a channel with no binding reaches no agent at
all, so a machine that added Telegram in the panel and never routed it answers
nobody. Only missing channels are proposed, bound to `main`; a channel the person
already routed is never touched, and a wildcard binding covers everything.

It lives beside `profile` rather than inside it because it is a different decision
from the roster and the models, and because the composer there is at its size
ceiling.
"""
from __future__ import annotations

from typing import Any


def missing_bindings(
    current: dict[str, Any] | None, entries: dict[str, Any],
) -> list[dict[str, Any]]:
    """The bindings to add: one per configured channel no binding routes."""
    if not isinstance(current, dict):
        return []
    channels = current.get("channels") if isinstance(current.get("channels"), dict) else {}
    if not channels or not entries:
        return []
    bindings = current.get("bindings") if isinstance(current.get("bindings"), list) else []
    bound: set[str] = set()
    for binding in bindings:
        match = binding.get("match") if isinstance(binding, dict) else None
        channel = match.get("channel") if isinstance(match, dict) else None
        if isinstance(channel, str):
            bound.add(channel)
    agent = "main" if "main" in entries else sorted(entries, key=str)[0]
    missing: list[dict[str, Any]] = []
    for name, block in sorted(channels.items()):
        if not isinstance(block, dict) or not block or block.get("enabled") is False:
            continue
        if str(name) in bound or "*" in bound:
            continue
        missing.append({"agentId": agent, "match": {"channel": str(name), "accountId": "*"}})
    return missing
