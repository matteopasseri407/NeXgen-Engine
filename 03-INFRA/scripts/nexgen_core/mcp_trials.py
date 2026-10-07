"""Trying an MCP server before committing to it: this machine only, behind the gateway, and it expires.

A server you only want to look at should not have to be added to the manifest (which every machine syncs) and
then remembered and removed. A trial is the other thing: written to this machine's state directory, never to the
Vault, served by the gateway like any lazy server, and gone by itself after its time (24 hours unless told
otherwise, a week at most). Because it is lazy it is never written into a CLI's configuration, so there is
nothing to find in four config files afterwards: expiry is the gateway ceasing to list it, with no restart.

`promote` turns a trial into a real manifest entry (which then syncs); `drop` ends it now.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from nexgen_core.files import write_private_text
from nexgen_core.i18n import t
from nexgen_core.mcp_placement import GATEWAY
from nexgen_core.paths import resolve_state_dir

DEFAULT_HOURS = 24.0
MAX_HOURS = 24.0 * 7
FILENAME = "mcp-trials.json"


def _path() -> Path:
    return resolve_state_dir() / FILENAME


def _load() -> dict[str, dict[str, Any]]:
    """Every recorded trial, expired or not. An unreadable file is no trials: it must never stop a render."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    trials = data.get("trials") if isinstance(data, dict) else None
    if not isinstance(trials, dict):
        return {}
    return {name: info for name, info in trials.items() if isinstance(info, dict) and isinstance(info.get("entry"), dict)
            and isinstance(info.get("expires"), (int, float))}


def _save(trials: dict[str, dict[str, Any]]) -> None:
    write_private_text(_path(), json.dumps({"schema_version": 1, "trials": trials}, indent=2, ensure_ascii=False) + "\n")


def active(now: float | None = None) -> dict[str, dict[str, Any]]:
    """name -> {entry, created, expires} for the trials still running."""
    moment = time.time() if now is None else now
    return {name: info for name, info in _load().items() if info["expires"] > moment}


def active_entries(now: float | None = None) -> dict[str, dict[str, Any]]:
    """name -> manifest-shaped entry, for the gateway and the plan."""
    return {name: {**info["entry"], "_trial_expires": info["expires"]} for name, info in active(now).items()}


def overlay(servers: dict[str, dict[str, Any]], now: float | None = None) -> dict[str, dict[str, Any]]:
    """The manifest's servers plus the running trials. The manifest wins a name clash: a trial never shadows a real server."""
    merged = dict(servers)
    for name, entry in active_entries(now).items():
        merged.setdefault(name, entry)
    return merged


def start(name: str, entry: dict[str, Any], hours: float, *, manifest_names: set[str], now: float | None = None) -> float:
    """Records a trial; returns when it expires. Raises ValueError for anything that should not start."""
    if not 0 < hours <= MAX_HOURS:
        raise ValueError(t("a trial lasts between a moment and {max:g} hours, not {hours:g}", max=MAX_HOURS, hours=hours))
    if name in manifest_names:
        raise ValueError(t("'{name}' is already in the manifest: a trial would only shadow it", name=name))
    moment = time.time() if now is None else now
    trials = {n: i for n, i in _load().items() if i["expires"] > moment}
    expires = moment + hours * 3600
    trials[name] = {"entry": entry, "created": moment, "expires": expires}
    _save(trials)
    return expires


def drop(name: str) -> bool:
    trials = _load()
    if name not in trials:
        return False
    del trials[name]
    _save(trials)
    return True


def purge_expired(now: float | None = None) -> list[str]:
    """Forgets the trials whose time is up. Nothing needs undoing elsewhere: a lazy trial lives in no CLI config."""
    moment = time.time() if now is None else now
    trials = _load()
    gone = sorted(name for name, info in trials.items() if info["expires"] <= moment)
    if gone:
        _save({n: i for n, i in trials.items() if n not in gone})
    return gone


def time_left(info: dict[str, Any], now: float | None = None) -> str:
    seconds = max(0, int(info["expires"] - (time.time() if now is None else now)))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}h{rest // 60:02d}m"


# ---------------------------------------------------------------------------------------------------- commands

def cmd_try(name: str, *, targets_raw: str | None, command: str | None, args: list[str] | None, url: str | None,
            auth_env: str | None, env_pairs: list[str] | None, readonly: bool, hours: float) -> tuple[int, str]:
    from nexgen_core.config import load_mcp_manifest
    from nexgen_core.mcp_add import _manifest_path, _parse_env, build_entry, parse_targets
    from nexgen_core.paths import resolve_vault_data

    try:
        entry = build_entry(name=name, targets=parse_targets(targets_raw), command=command, args=args, url=url,
                            auth_env=auth_env, env=_parse_env(env_pairs), lazy=True, readonly=readonly)
        path = _manifest_path(resolve_vault_data())
        names = set(load_mcp_manifest(path).get("servers", {})) if path.is_file() else set()
        if GATEWAY not in names:
            return 2, t(
                "The gateway ({gateway}) is not in your manifest, and a trial is served by it. Adding any server with "
                "'nexgen mcp add' puts it in; or copy the {gateway} entry from the shipped template.",
                gateway=GATEWAY,
            )
        expires = start(name, entry, hours, manifest_names=names)
    except ValueError as exc:
        return 2, str(exc)
    return 0, t(
        "{name} is on trial for {hours:g}h on this machine only: it is served by the gateway (no CLI config is touched, "
        "nothing syncs) and disappears by itself. Keep it with 'nexgen mcp promote {name}', end it with 'nexgen mcp drop {name}'.",
        name=name, hours=(expires - time.time()) / 3600,
    )


def cmd_trials() -> int:
    running = active()
    if not running:
        print(t("No MCP servers on trial."))
        return 0
    for name, info in sorted(running.items()):
        entry = info["entry"]
        what = entry.get("url") or " ".join([str(entry.get("command", "")), *map(str, entry.get("args", []))])
        print(f"  {name}: " + t("{left} left", left=time_left(info)) + f"  ({what})")
    return 0


def cmd_drop(name: str) -> tuple[int, str]:
    if drop(name):
        return 0, t("{name}: trial ended.", name=name)
    return 1, t("{name} is not on trial here.", name=name)


def cmd_promote(name: str) -> tuple[int, str]:
    """Moves a running trial into the manifest, where it syncs to every machine."""
    from nexgen_core.mcp_add import insert_entry

    info = active().get(name)
    if info is None:
        return 1, t("{name} is not on trial here (it may have expired).", name=name)
    entry = {k: v for k, v in info["entry"].items() if not k.startswith("_")}
    code, message = insert_entry(name, entry)
    if code == 0:
        drop(name)
    return code, message
