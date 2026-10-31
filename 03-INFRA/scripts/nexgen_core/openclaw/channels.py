"""Which plugin serves the channel a squad binds, and the allowlist entry it needs.

One job: name the plugin id OpenClaw ships for a channel, and write a squad's
`bindings` together with those plugin ids, so a setup never leaves OpenClaw to add
the plugin itself on the next `doctor --fix` and then fight it about the same list.

The ids are OpenClaw's own, read from its bundled channel table (2026.10.7): every
built-in channel is served by the plugin of the same id, and the table carries the
aliases people write (`teams` -> `msteams`). A channel no bundled plugin serves
belongs to a plugin the person installed and allowed themselves; its id cannot be
derived from here, so nothing is invented for it.
"""
from __future__ import annotations

from typing import Any

#: The channel ids OpenClaw ships a bundled plugin for; the plugin id is the id itself.
BUNDLED_CHANNELS: tuple[str, ...] = (
    "a2a", "buzz", "clickclack", "discord", "feishu", "googlechat", "imessage", "irc",
    "line", "matrix", "mattermost", "msteams", "nextcloud-talk", "nostr", "raft", "reef",
    "signal", "slack", "sms", "synology-chat", "telegram", "tlon", "twitch", "whatsapp",
    "x", "zalo", "zalouser",
)

#: The other names OpenClaw's table accepts for a channel, mapped to the id it stores.
CHANNEL_ALIASES: dict[str, str] = {
    "lark": "feishu",
    "gchat": "googlechat",
    "google-chat": "googlechat",
    "imsg": "imessage",
    "internet-relay-chat": "irc",
    "teams": "msteams",
    "nc": "nextcloud-talk",
    "nc-talk": "nextcloud-talk",
    "twitch-chat": "twitch",
    "zl": "zalo",
    "zlu": "zalouser",
}


def plugin_id(channel: Any) -> str | None:
    """The plugin OpenClaw ships for this channel, or None when it ships none."""
    name = str(channel if channel is not None else "").strip().lower()
    name = CHANNEL_ALIASES.get(name, name)
    return name if name in BUNDLED_CHANNELS else None


def bound_channels(bindings: Any) -> list[str]:
    """The channels a list of bindings routes to, once each, in the order they appear."""
    channels: list[str] = []
    for binding in bindings if isinstance(bindings, list) else []:
        match = binding.get("match") if isinstance(binding, dict) else None
        channel = match.get("channel") if isinstance(match, dict) else None
        name = str(channel).strip() if isinstance(channel, str) and channel.strip() else ""
        if name and name not in channels:
            channels.append(name)
    return channels


def with_bindings(config: dict[str, Any], bindings: list[Any]) -> dict[str, Any]:
    """The proposal carrying those bindings, and the plugin of every channel they name.

    The bindings are written as the squad declares them; only the allowlist grows, and
    only by ids OpenClaw itself would have added for those channels. A proposal with no
    `plugins` section keeps none unless a channel really brought a plugin with it, so an
    empty allowlist is never invented.
    """
    plugins = dict(config.get("plugins") or {})
    allow = list(plugins.get("allow") or [])
    added = False
    for channel in bound_channels(bindings):
        plugin = plugin_id(channel)
        if plugin and plugin not in allow:
            allow.append(plugin)
            added = True
    written = {**config, "bindings": list(bindings)}
    if added or "plugins" in config:
        written["plugins"] = {**plugins, "allow": allow}
    return written
