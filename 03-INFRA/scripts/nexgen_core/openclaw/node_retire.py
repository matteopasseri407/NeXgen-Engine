"""Preparing a machine that becomes a node, and retiring what its gateway left.

One job: put the local OpenClaw configuration in the state a node needs, and
move aside what a local gateway leaves behind, in one write and one report.

Three leftovers made a node fight the central gateway, all measured on real
machines on 2026-10-26. A systemd drop-in left without its unit is read by the
companion app as a changed service definition, and its setup refuses to go on.
The gateway's own `auth` block makes the node send that token instead of its
pairing token, and the central gateway answers `AUTH_TOKEN_MISMATCH` until the
file is edited by hand. Enabled channels make two gateways poll one bot, and
they kill each other's poller every thirty seconds.

The one thing this module adds is the opposite direction: `nodeHost.workerRuns`
switched on, because a node that cannot host sessions is half a node.

The browser is the second: a node runs the shared Chrome the engine keeps, so the
`nexgen` profile is added to point OpenClaw at it instead of at a browser of its
own. That block comes from `browser_profile`, and `prepare_config` only publishes
what it returns.

Nothing here deletes state. Sessions, memory and every other key stay; the
configuration is copied aside before it is touched, and the drop-in is moved
rather than removed.
"""
from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.megaphone import OPENCLAW_COMMAND
from nexgen_core.openclaw import config_write, node_cli, setup_write
from nexgen_core.openclaw.browser_profile import plan_node_browser, shared_chrome_ready
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.node_cli import Run
from nexgen_core.openclaw.node_transition import stop_local_gateway_safe
from nexgen_core.openclaw_service import RunResult
from nexgen_core.paths import openclaw_config, resolve_home

#: `(argv, env) -> RunResult`: the executor `config_patch` calls, injectable for a test.
PatchRun = Callable[[list[str], Mapping[str, str]], RunResult]

#: The unit the local gateway left behind, and the drop-in directory beside it.
GATEWAY_DROP_IN = "openclaw-gateway.service.d"

#: Where the retired pieces go, inside the state directory the person already has.
ARCHIVE_DIR = "backups"


@dataclass(frozen=True)
class ConfigChange:
    """What the configuration lost or gained, and where the previous copy lives."""

    auth_removed: bool
    channels_disabled: tuple[str, ...]
    session_hosting_enabled: bool
    backup: Path | None
    browser_profile_added: bool = False


@dataclass(frozen=True)
class RetireResult:
    """The whole retirement: the service, the leftovers, the configuration."""

    drop_in_archive: Path | None
    config: ConfigChange


def _stamp() -> str:
    """A name that sorts by time and never collides with the next run."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def drop_in_path(
    home: Path | None = None,
    systemd_user_dir: Path | None = None,
) -> Path:
    """Where the retired unit's drop-in would be: one answer, so the plan and the move agree."""
    resolved_home = resolve_home(home)
    directory = systemd_user_dir or (resolved_home / ".config" / "systemd" / "user")
    return directory / GATEWAY_DROP_IN


def archive_drop_in(
    home: Path | None = None,
    systemd_user_dir: Path | None = None,
    out: Callable[[str], None] = print,
) -> Path | None:
    """Move a drop-in left without its unit into the state directory, and say where.

    A drop-in alone changes nothing at boot, because systemd needs the unit. It
    still makes a service definition look present to anything that inspects the
    directory, which is exactly what the companion app's setup does. Moving it
    keeps the person's hardening settings for the day a gateway runs here again.
    """
    resolved_home = resolve_home(home)
    drop_in = drop_in_path(resolved_home, systemd_user_dir)
    if not drop_in.is_dir():
        return None
    target = resolved_home / ".openclaw" / ARCHIVE_DIR / f"retired-gateway-{_stamp()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(drop_in), str(target))
    out(t("Gateway drop-in left without its unit, moved to {path}.", path=target))
    return target


def _without_gateway_auth(document: dict[str, Any]) -> dict[str, Any] | None:
    """The patch that takes the gateway's own token out, or None when it carries none.

    `gateway.auth: null` is how a patch deletes a path: a node has its pairing
    token instead, and a leftover local one makes it send that instead.
    """
    gateway = document.get("gateway")
    if not isinstance(gateway, dict) or "auth" not in gateway:
        return None
    return {"auth": None}


def _channels_off(document: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """The patch that switches every enabled channel off, and their names.

    Merge semantics keep the rest of each channel's settings: only `enabled` is
    said, so nothing else the person configured is rewritten.
    """
    channels = document.get("channels")
    if not isinstance(channels, dict):
        return {}, ()
    disabled = [
        str(name) for name, entry in channels.items()
        if isinstance(entry, dict) and entry.get("enabled") is True
    ]
    if not disabled:
        return {}, ()
    return {name: {"enabled": False} for name in disabled}, tuple(disabled)


def _session_hosting(document: dict[str, Any]) -> dict[str, Any] | None:
    """The patch that lets this node host sessions, or None when it already can."""
    node_host = document.get("nodeHost")
    worker_runs = node_host.get("workerRuns") if isinstance(node_host, dict) else None
    if isinstance(worker_runs, dict) and worker_runs.get("enabled") is True:
        return None
    return {"workerRuns": {"enabled": True}}


def _home_of(path: Path, home: Path | None) -> Path:
    """The home this configuration belongs to: the caller's, or the one the file sits under."""
    return Path(home) if home is not None else path.parent.parent


def _say(notes: tuple[str, ...], out: Callable[[str], None]) -> None:
    """One line per note, in the order the nodes were decided."""
    for note in notes:
        out(note)


def _default_run(argv: list[str], env: Mapping[str, str]) -> RunResult:
    """Run one OpenClaw command on this platform, with the node's own deadline."""
    return node_cli.run_command(argv, config_write.PATCH_TIMEOUT_S, env=env)


def prepare_config(
    path: Path,
    out: Callable[[str], None] = print,
    *,
    home: Path | None = None,
    run: PatchRun | None = None,
) -> ConfigChange:
    """Put the configuration in node state with one read and one validated patch.

    The node's state is expressed as a patch and handed to `openclaw config patch`:
    it merges the change into the file, validates the result, keeps the previous
    file as `openclaw.json.bak` beside it and refuses without touching anything
    when it does not like the result. Deleting `gateway.auth` is `null` at that
    path; `browser` is replaced whole, because the profile this node needs is the
    section the engine decided, not a merge into one it does not own.
    """
    document, reason = read_config(path)
    if document is None:
        if reason:
            out(t("Configuration not readable, left as it is: {reason}", reason=reason))
        return ConfigChange(False, (), False, None)

    auth = _without_gateway_auth(document)
    channels, disabled = _channels_off(document)
    hosting = _session_hosting(document)
    browser = plan_node_browser(document, ready=shared_chrome_ready(_home_of(path, home)))
    patch: dict[str, Any] = {}
    if auth is not None:
        patch["gateway"] = auth
    if channels:
        patch["channels"] = channels
    if hosting is not None:
        patch["nodeHost"] = hosting
    if browser.section is not None:
        patch["browser"] = browser.section
    if not patch:
        _say(browser.notes, out)
        return ConfigChange(False, (), False, None)

    verdict = _apply_patch(path, patch, run)
    if not verdict.ok:
        out(t("OpenClaw refused the node configuration and left the file as it was: {reason}",
              reason=verdict.detail))
        return ConfigChange(False, (), False, None)
    if auth is not None:
        out(t("Gateway auth block removed from {path}: the node uses its pairing token.", path=path))
    if disabled:
        out(t("Channels switched off on this PC, they answer from the central gateway: {names}.",
              names=", ".join(disabled)))
    if hosting is not None:
        out(t("Session hosting enabled: this node can run agent sessions, not only commands."))
    _say(browser.notes, out)
    return ConfigChange(
        auth is not None, disabled, hosting is not None, config_write.backup_path(path),
        browser.section is not None,
    )


def _apply_patch(path: Path, patch: dict[str, Any], run: PatchRun | None) -> config_write.Verdict:
    """Write the patch beside the file, have OpenClaw apply it, and clean up after."""
    replace_paths = ["browser"] if "browser" in patch else []
    config_write.write_patch(path, setup_write.render(patch))
    try:
        return config_write.apply(run or _default_run, OPENCLAW_COMMAND, path, os.environ, replace_paths)
    finally:
        config_write.clear_patch(path)


def retire_local_gateway(
    home: Path | None = None,
    platform: str | None = None,
    runner: Run | None = None,
    appdata: Path | None = None,
    startup_dir: Path | None = None,
    systemd_user_dir: Path | None = None,
    config_path: Path | None = None,
    out: Callable[[str], None] = print,
) -> RetireResult:
    """Stop and remove the local gateway service, then clean what it left behind."""
    plat = platform if platform is not None else sys.platform
    resolved_home = resolve_home(home)
    stop_local_gateway_safe(
        home=resolved_home,
        platform=plat,
        runner=runner,
        appdata=appdata,
        startup_dir=startup_dir,
        systemd_user_dir=systemd_user_dir,
        out=out,
    )
    archive = None if plat == "win32" else archive_drop_in(resolved_home, systemd_user_dir, out)
    change = prepare_config(config_path or openclaw_config(resolved_home), out, home=resolved_home)
    return RetireResult(drop_in_archive=archive, config=change)


def report(result: RetireResult) -> str:
    """One line a person can read without the code in front of them."""
    parts: list[str] = []
    if result.drop_in_archive is not None:
        parts.append(t("drop-in archived"))
    if result.config.auth_removed:
        parts.append(t("gateway auth removed"))
    if result.config.channels_disabled:
        parts.append(t("channels off: {names}", names=", ".join(result.config.channels_disabled)))
    if result.config.session_hosting_enabled:
        parts.append(t("session hosting on"))
    if result.config.browser_profile_added:
        parts.append(t("browser profile added"))
    if not parts:
        return t("Nothing left behind by a local gateway.")
    return t("Local gateway retired: {parts}.", parts=", ".join(parts))


def describe_config_change(path: Path, home: Path | None = None) -> list[str]:
    """What `prepare_config` would change, for a dry run: read only, no write."""
    document, _reason = read_config(path)
    if document is None:
        return []
    auth = _without_gateway_auth(document)
    _channels, disabled = _channels_off(document)
    hosting = _session_hosting(document)
    browser = plan_node_browser(document, ready=shared_chrome_ready(_home_of(path, home)))
    lines: list[str] = []
    if auth is not None:
        lines.append(t("- gateway auth block: removed, the node uses its pairing token"))
    if disabled:
        lines.append(t("- channels switched off here: {names}", names=", ".join(disabled)))
    if hosting is not None:
        lines.append(t("- session hosting: switched on, so this node can run agent sessions"))
    lines.extend(f"- {note}" for note in browser.notes)
    return lines
