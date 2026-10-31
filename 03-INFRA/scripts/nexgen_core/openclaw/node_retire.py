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

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.browser_profile import plan_node_browser, shared_chrome_ready
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.node_cli import Run
from nexgen_core.openclaw.node_transition import stop_local_gateway_safe
from nexgen_core.openclaw.setup_write import backup_file, render, write_document
from nexgen_core.paths import openclaw_config, resolve_home

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


def _strip_gateway_auth(document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """The document without the gateway's own token: a node has its pairing token instead."""
    gateway = document.get("gateway")
    if not isinstance(gateway, dict) or "auth" not in gateway:
        return document, False
    stripped = {key: value for key, value in gateway.items() if key != "auth"}
    return {**document, "gateway": stripped}, True


def _switch_channels_off(document: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Every enabled channel off: a channel lives on one gateway at a time, the central one."""
    channels = document.get("channels")
    if not isinstance(channels, dict):
        return document, ()
    updated: dict[str, Any] = {}
    disabled: list[str] = []
    for name, entry in channels.items():
        if isinstance(entry, dict) and entry.get("enabled") is True:
            updated[name] = {**entry, "enabled": False}
            disabled.append(str(name))
        else:
            updated[name] = entry
    if not disabled:
        return document, ()
    return {**document, "channels": updated}, tuple(disabled)


def _enable_session_hosting(document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """`nodeHost.workerRuns.enabled: true`, because a node that hosts no sessions is half a node."""
    node_host = document.get("nodeHost")
    node_host = dict(node_host) if isinstance(node_host, dict) else {}
    worker_runs = node_host.get("workerRuns")
    worker_runs = dict(worker_runs) if isinstance(worker_runs, dict) else {}
    if worker_runs.get("enabled") is True:
        return document, False
    worker_runs["enabled"] = True
    node_host["workerRuns"] = worker_runs
    return {**document, "nodeHost": node_host}, True


def _home_of(path: Path, home: Path | None) -> Path:
    """The home this configuration belongs to: the caller's, or the one the file sits under."""
    return Path(home) if home is not None else path.parent.parent


def _say(notes: tuple[str, ...], out: Callable[[str], None]) -> None:
    """One line per note, in the order the nodes were decided."""
    for note in notes:
        out(note)


def prepare_config(
    path: Path,
    out: Callable[[str], None] = print,
    *,
    home: Path | None = None,
) -> ConfigChange:
    """Put the configuration in node state with one read, one backup and one write."""
    document, reason = read_config(path)
    if document is None:
        if reason:
            out(t("Configuration not readable, left as it is: {reason}", reason=reason))
        return ConfigChange(False, (), False, None)

    document, auth_removed = _strip_gateway_auth(document)
    document, disabled = _switch_channels_off(document)
    document, hosting = _enable_session_hosting(document)
    browser = plan_node_browser(document, ready=shared_chrome_ready(_home_of(path, home)))
    if browser.section is not None:
        document = {**document, "browser": browser.section}
    if not (auth_removed or disabled or hosting or browser.section is not None):
        _say(browser.notes, out)
        return ConfigChange(False, (), False, None)

    backup = backup_file(path, _stamp())
    write_document(path, render(document))
    if auth_removed:
        out(t("Gateway auth block removed from {path}: the node uses its pairing token.", path=path))
    if disabled:
        out(t("Channels switched off on this PC, they answer from the central gateway: {names}.",
              names=", ".join(disabled)))
    if hosting:
        out(t("Session hosting enabled: this node can run agent sessions, not only commands."))
    _say(browser.notes, out)
    return ConfigChange(auth_removed, disabled, hosting, backup, browser.section is not None)


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
    document_after, auth_removed = _strip_gateway_auth(document)
    _document_after, disabled = _switch_channels_off(document_after)
    _document_after, hosting = _enable_session_hosting(document_after)
    browser = plan_node_browser(document_after, ready=shared_chrome_ready(_home_of(path, home)))
    lines: list[str] = []
    if auth_removed:
        lines.append(t("- gateway auth block: removed, the node uses its pairing token"))
    if disabled:
        lines.append(t("- channels switched off here: {names}", names=", ".join(disabled)))
    if hosting:
        lines.append(t("- session hosting: switched on, so this node can run agent sessions"))
    lines.extend(f"- {note}" for note in browser.notes)
    return lines
