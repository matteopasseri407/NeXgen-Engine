"""The local agy CLI backend plugin: its permission modes, and the entry this engine writes.

One job: name the modes the `google-antigravity-cli` plugin understands, decide
which one the engine puts in the configuration it owns, and say what the plugin's
entry holds when the team asks for the backend. `skip` hands tool approval to
`agy` itself and is the plugin's default when nothing is declared, so the engine
never writes it: a machine whose model approves its own tool calls is a machine
that runs whatever the model asks for. `sandbox` and `settings` keep a boundary,
and a guarded mode already in force is left alone, because the person may have
chosen `sandbox` or `settings` deliberately.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

#: The plugin's own mode that lets `agy` approve every tool call by itself.
AUTO_APPROVING_MODE = "skip"
#: The mode this engine writes when nothing guarded is in force.
SANDBOX_MODE = "sandbox"
#: The other mode that keeps a boundary: `agy` decides from its own settings file.
SETTINGS_MODE = "settings"

#: Every mode that keeps an approval boundary in place.
GUARDED_MODES: tuple[str, ...] = (SANDBOX_MODE, SETTINGS_MODE)


def _entry(current: dict[str, Any] | None, backend: str) -> dict[str, Any] | None:
    """The plugin's own entry in the configuration in force, or None when it has none."""
    plugins = (current or {}).get("plugins")
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    entry = entries.get(backend) if isinstance(entries, dict) else None
    return entry if isinstance(entry, dict) else None


def declared_mode(current: dict[str, Any] | None, backend: str) -> str | None:
    """The permission mode the file in force declares for `backend`, or None when it declares none."""
    entry = _entry(current, backend)
    config = entry.get("config") if entry else None
    value = config.get("permissionMode") if isinstance(config, dict) else None
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def mode_for_engine(current: dict[str, Any] | None, backend: str) -> str:
    """The mode the engine writes: a guarded one already in force, else `sandbox`, never `skip`."""
    declared = declared_mode(current, backend)
    return declared if declared in GUARDED_MODES else SANDBOX_MODE


def approved(current: dict[str, Any] | None, backend: str = LOCAL_AGY_BACKEND) -> bool:
    """Whether the person already allowed this backend and did not switch it off.

    Approval in the file in force is what keeps a backend the person chose: the
    engine aligns its one setting and never removes it. It is no longer the only
    way in, since a team that runs an agent on `agy` is itself the request
    (`with_backend`); an entry that is absent is still not approval, and neither
    is an entry that is present and disabled.
    """
    plugins = (current or {}).get("plugins")
    plugins = plugins if isinstance(plugins, dict) else {}
    allowed = plugins.get("allow") if isinstance(plugins.get("allow"), list) else []
    entry = _entry(current, backend) or {}
    return backend in {str(item) for item in allowed} and entry.get("enabled") is not False


def with_backend(config: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
    """The configuration with this backend allowed, enabled and given a guarded mode.

    Called when the squad puts an agent on `agy`: that is the owner asking for the
    plugin, so the engine introduces it instead of waiting to find it approved.
    Only `permissionMode` is carried over from the file in force -- a guarded mode
    the person chose stays, anything else becomes `sandbox` -- and every other key
    of the entry is kept, because the rest of the plugin's settings are not the
    engine's.
    """
    plugins = dict(config.get("plugins") or {})
    allow = list(plugins.get("allow") or [])
    if LOCAL_AGY_BACKEND not in allow:
        allow.append(LOCAL_AGY_BACKEND)
    entries = dict(plugins.get("entries") or {})
    entry = dict(entries.get(LOCAL_AGY_BACKEND) or {})
    settings = dict(entry.get("config") or {})
    settings["permissionMode"] = mode_for_engine(current, LOCAL_AGY_BACKEND)
    entry.update({"enabled": True, "config": settings})
    entries[LOCAL_AGY_BACKEND] = entry
    return {**config, "plugins": {**plugins, "allow": allow, "entries": entries}}
