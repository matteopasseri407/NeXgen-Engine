"""Which OpenClaw plugins may read the conversation: the grant the engine writes for each one.

One job: write `hooks.allowConversationAccess` for the engine's own guardrail plugin,
and for the `google-antigravity-cli` plugin when the setup has it installed and enabled.
OpenClaw withholds the conversation hooks from every plugin not bundled with it unless
this grant is written. Without it the Antigravity plugin's `before_prompt_build` hook is
blocked, so `agy` never gets its lost turns back.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND
from nexgen_core.runtimes.openclaw import PLUGIN_ID


def _granted(config: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    """The configuration with the conversation grant merged into one plugin's `hooks` block.

    A key the person wrote in that block is never removed, and nothing else in the entry changes.
    """
    plugins = dict(config.get("plugins") or {})
    entries = dict(plugins.get("entries") or {})
    entry = dict(entries.get(plugin_id) or {})
    hooks = dict(entry.get("hooks") or {}) if isinstance(entry.get("hooks"), dict) else {}
    hooks["allowConversationAccess"] = True
    entry["hooks"] = hooks
    entries[plugin_id] = entry
    return {**config, "plugins": {**plugins, "entries": entries}}


def _with_antigravity_grant(config: dict[str, Any], installed: bool) -> dict[str, Any]:
    """The grant for the Antigravity plugin, only when it is installed here and enabled in the config.

    The setup installs the pinned plugin before it writes a config that enables it, so an enabled
    entry means the plugin is there. Nothing is granted to a plugin the engine does not run.
    """
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    entry = entries.get(LOCAL_AGY_BACKEND) if isinstance(entries.get(LOCAL_AGY_BACKEND), dict) else {}
    if not installed or entry.get("enabled") is not True:
        return config
    return _granted(config, LOCAL_AGY_BACKEND)


def with_engine_plugin(config: dict[str, Any], deposited: bool, agy_installed: bool = False) -> dict[str, Any]:
    """The engine's guardrail plugin, enabled, and allowed to read the conversation.

    Enabling a plugin that is not there makes OpenClaw fail to start; the
    plugin is deposited by the runtime adapter, which is the only thing that
    knows how. The plan and the setup therefore share this one decision, so
    what the plan prints is what the setup writes.

    Two of the engine's hooks are conversation hooks, and both go quiet without the
    grant: `before_model_resolve`, where the governor moves a turn off a spent plan, and
    the turn's messages and finished answer, which the evidence check judges. The plugin
    is the engine's own and already runs inside the gateway process; this is what makes
    its registration legal. The Antigravity plugin gets the same grant when it is installed
    and enabled (`agy_installed`), so its own turn hook is not withheld either.
    """
    config = _with_antigravity_grant(config, agy_installed)
    if not deposited:
        return config
    plugins = dict(config.get("plugins") or {})
    allow = list(plugins.get("allow") or [])
    if PLUGIN_ID not in allow:
        allow.append(PLUGIN_ID)
    entries = dict(plugins.get("entries") or {})
    entry = dict(entries.get(PLUGIN_ID) or {})
    entry["enabled"] = True
    entries[PLUGIN_ID] = entry
    return _granted({**config, "plugins": {**plugins, "allow": allow, "entries": entries}}, PLUGIN_ID)
