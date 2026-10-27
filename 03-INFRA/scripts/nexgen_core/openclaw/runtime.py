"""Runtime defaults the squad owns, applied to the OpenClaw config.

One job: turn the `runtime` block of squad.yaml into the OpenClaw settings that
decide how many chats an agent has, how long a manual fork survives, and which
model does the housekeeping. Those settings are policy, not taste: without them
a reinstall silently loses "one chat per agent" and the person is back to a
sidebar full of stale threads.

The block is deliberately small and validated. A typo must fail the setup, not
write a key OpenClaw ignores.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: Every key the block accepts, and the config path it writes.
_KEYS: dict[str, tuple[str, ...]] = {
    "dm_scope": ("session", "dmScope"),
    "archive_forks_after": ("session", "maintenance", "archiveDashboardAfter"),
    "automation_session_retention": ("cron", "sessionRetention"),
    "mcp_idle_ttl": ("mcp", "sessionIdleTtlMs"),
    "subagent_model": ("agents", "defaults", "subagents", "model"),
    "compaction_model": ("agents", "defaults", "compaction", "model"),
}

_DM_SCOPES = {"main", "per-peer", "per-channel-peer", "per-account-channel-peer"}
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)$")
_UNIT_MS = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}


def _duration_ms(value: Any) -> int:
    """A duration in milliseconds, from `24h`, `5m`, or a plain number of ms."""
    if isinstance(value, bool):
        raise ValueError("una durata non puo' essere un booleano")
    if isinstance(value, (int, float)):
        if value <= 0:
            raise ValueError(f"durata non positiva: {value}")
        return int(value)
    match = _DURATION.match(str(value).strip().lower())
    if not match:
        raise ValueError(f"durata non riconosciuta: {value!r} (usa 30m, 2h, 24h)")
    amount, unit = match.groups()
    result = int(float(amount) * _UNIT_MS[unit])
    if result <= 0:
        raise ValueError(f"durata non positiva: {value!r}")
    return result


def _normalized(key: str, value: Any) -> Any:
    if key == "dm_scope":
        scope = str(value).strip()
        if scope not in _DM_SCOPES:
            raise ValueError(f"dm_scope sconosciuto: {value!r}")
        return scope
    if key == "mcp_idle_ttl":
        return _duration_ms(value)
    if key == "archive_forks_after":
        if value is False:
            return False
        return str(value).strip().lower()
    return str(value).strip()


def _put(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = config
    for part in path[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[path[-1]] = value


def apply_runtime(config: dict[str, Any], runtime: Mapping[str, Any]) -> dict[str, Any]:
    """Writes the squad runtime block into an OpenClaw config, in place."""
    unknown = sorted(set(runtime) - set(_KEYS))
    if unknown:
        raise ValueError(f"chiavi runtime sconosciute: {', '.join(unknown)}")
    for key, raw in runtime.items():
        value = _normalized(key, raw)
        _put(config, _KEYS[key], value)
        if key == "archive_forks_after" and value is not False:
            # Archiving only reports in warn mode: the duration alone would do nothing.
            _put(config, ("session", "maintenance", "mode"), "enforce")
    return config
