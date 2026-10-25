"""The local agy CLI backend plugin: its permission modes, and the one this engine writes.

One job: name the modes the `google-antigravity-cli` plugin understands, and decide
which one the engine puts in the configuration it owns. `skip` hands tool approval
to `agy` itself and is the plugin's default when nothing is declared, so the engine
never writes it: a machine whose model approves its own tool calls is a machine that
runs whatever the model asks for. `sandbox` and `settings` keep a boundary, and a
guarded mode already in force is left alone, because the person may have chosen
`sandbox` or `settings` deliberately.
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

    The engine never introduces the plugin: it only keeps, aligns and reports what the
    person approved. Allowing it while its entry is disabled is not approval, and an entry
    that is absent is not approval either.
    """
    plugins = (current or {}).get("plugins")
    plugins = plugins if isinstance(plugins, dict) else {}
    allowed = plugins.get("allow") if isinstance(plugins.get("allow"), list) else []
    entry = _entry(current, backend) or {}
    return backend in {str(item) for item in allowed} and entry.get("enabled") is not False
