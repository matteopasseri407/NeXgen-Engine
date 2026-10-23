"""The live state around an installed OpenClaw: ports, other gateways, learned skills.

One job: the checks that read machine state rather than configuration, so
`doctor.py` stays the rules of the configuration and this module answers what is
running, what else is running, and what an agent has learned. Read-only by
construction: no command is executed, no file is written, and a URL read from
the Companion's settings is never printed.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nexgen_core.i18n import t
from nexgen_core.openclaw import host_preflight
from nexgen_core.openclaw.pin import node_satisfies
from nexgen_core.openclaw.profile import GATEWAY_PORT
from nexgen_core.report import CheckOutcome, Severity

DEFAULT_GATEWAY_PORT = GATEWAY_PORT
LISTEN_TIMEOUT_SECONDS = 0.4
#: The window the learned-skills line speaks about, and the count that is too many.
WORKSHOP_WINDOW_SECONDS = 24 * 60 * 60
WORKSHOP_WARN_OVER = 10
#: The Windows Companion's own gateway and where it keeps it.
COMPANION_DISTRO = "OpenClawGateway"
COMPANION_DIRNAME = "OpenClawTray"

Connect = Callable[[str, int, float], bool]


def default_connect(host: str, port: int, timeout: float) -> bool:
    """Whether something accepts a TCP connection there, without sending a byte."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:  # nexgen: allow reason="a closed port is the expected answer when the gateway is stopped"
        return False


def check_listening(config: dict[str, Any], connect: Connect) -> CheckOutcome:
    """Informational: a gateway that is stopped is a normal state, never an error."""
    gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
    port = gateway.get("port") if isinstance(gateway.get("port"), int) else DEFAULT_GATEWAY_PORT
    if connect("127.0.0.1", int(port), LISTEN_TIMEOUT_SECONDS):
        return CheckOutcome(
            id="openclaw.gateway.listening",
            severity=Severity.OK,
            message=t("The gateway answers on 127.0.0.1:{port}.", port=port),
        )
    return CheckOutcome(
        id="openclaw.gateway.listening",
        severity=Severity.OK,
        message=t("The gateway is not listening on 127.0.0.1:{port} (normal when it is stopped).", port=port),
    )


def current_node_version() -> str | None:
    """The Node on PATH, asked for its version, or None when it cannot answer."""
    import shutil
    import subprocess

    from nexgen_core.processes import no_window_kwargs

    node = shutil.which("node")
    if not node:
        return None
    try:
        done = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="a Node that cannot be asked is an unknown version, not a crash"
        return None
    text = (done.stdout or "").strip()
    return text.splitlines()[-1].strip() if text else None


def check_node(requirement: str | None, version: str | None) -> CheckOutcome | None:
    """OpenClaw declares the Node it runs on; a different major is a broken install."""
    if not requirement:
        return None
    if version is None:
        return CheckOutcome(
            id="openclaw.node",
            severity=Severity.WARN,
            message=t("The Node version cannot be read, so OpenClaw's requirement ({requirement}) is unverified.",
                      requirement=requirement),
            action=t("Make sure 'node --version' answers in this shell, then run 'nexgen doctor' again."),
        )
    if node_satisfies(version, requirement):
        return CheckOutcome(
            id="openclaw.node",
            severity=Severity.OK,
            message=t("Node {version} satisfies OpenClaw's requirement ({requirement}).",
                      version=version, requirement=requirement),
        )
    return CheckOutcome(
        id="openclaw.node",
        severity=Severity.BROKEN,
        message=t("Node {version} does not satisfy OpenClaw's requirement ({requirement}).",
                  version=version, requirement=requirement),
        action=t("Install a Node release in that range from {url}, then run 'nexgen doctor' again.",
                 url="https://nodejs.org/en/download"),
    )


def check_linger(platform: str, user: str | None, directory: Path | None = None) -> CheckOutcome | None:
    """On Linux, a service without lingering dies with the last session."""
    if not platform.startswith("linux") or not user:
        return None
    if host_preflight.linger_enabled(user, directory or host_preflight.LINGER_DIR):
        return CheckOutcome(
            id="openclaw.linger",
            severity=Severity.OK,
            message=t("Lingering is enabled for {user}: the gateway survives logout.", user=user),
        )
    return CheckOutcome(
        id="openclaw.linger",
        severity=Severity.WARN,
        message=t("Lingering is off for {user}: the gateway stops when the last session closes.", user=user),
        action=t("Run 'loginctl enable-linger {user}' once.", user=user),
    )


def check_bindings(config: dict[str, Any]) -> CheckOutcome | None:
    """With explicit ownership, a channel without a binding reaches no agent."""
    channels = config.get("channels") if isinstance(config.get("channels"), dict) else {}
    configured = {
        str(name): block for name, block in channels.items()
        if isinstance(block, dict) and block and block.get("enabled") is not False
    }
    if not configured:
        return None
    bindings = config.get("bindings") if isinstance(config.get("bindings"), list) else []
    bound: set[str] = set()
    for binding in bindings:
        match = binding.get("match") if isinstance(binding, dict) else None
        channel = match.get("channel") if isinstance(match, dict) else None
        if isinstance(channel, str):
            bound.add(channel)
    missing = sorted(name for name in configured if name not in bound and "*" not in bound)
    if missing:
        return CheckOutcome(
            id="openclaw.bindings",
            severity=Severity.WARN,
            message=t("These configured channels have no agent binding: {names}. "
                      "With explicit ownership their messages reach no agent.", names=", ".join(missing)),
            action=t("Run 'nexgen openclaw setup': it binds them to the main agent and leaves your bindings untouched."),
        )
    return CheckOutcome(
        id="openclaw.bindings",
        severity=Severity.OK,
        message=t("Every configured channel has an agent binding."),
    )


def store_packages(home: Path, env: Mapping[str, str], platform: str) -> list[str]:
    """The Microsoft Store package of OpenClaw, when Windows installed one."""
    if not platform.startswith("win"):
        return []
    local_appdata = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
    packages = Path(local_appdata) / "Packages"
    if not packages.is_dir():
        return []
    return sorted(
        child.name for child in packages.iterdir()
        if child.is_dir() and "openclaw" in child.name.lower()
    )


def check_store_gateway(home: Path, env: Mapping[str, str], platform: str) -> CheckOutcome | None:
    """Two gateways on the same machine fight over the same port and state."""
    found = store_packages(home, env, platform)
    if not found:
        return None
    return CheckOutcome(
        id="openclaw.store_gateway",
        severity=Severity.WARN,
        message=t("A second OpenClaw from the Microsoft Store is installed ({names}): two gateways in conflict.",
                  names=", ".join(found)),
        action=t("Remove the Microsoft Store app: the engine manages the npm one."),
    )


# --------------------------------------------------------------- learned skills


def _agent_path(value: str, home: Path, base: Path) -> Path:
    """A path from OpenClaw's own config, with `~` expanded against `home`."""
    if value == "~":
        return home
    if value.startswith(("~/", "~\\")):
        return home / value[2:]
    candidate = Path(value)
    return candidate if candidate.is_absolute() else base / candidate


def _workshop_dir(state_dir: Path, home: Path, agent_id: str, entry: Any) -> Path:
    """Where Skill Workshop keeps this agent's learned skills.

    OpenClaw's default is `<state dir>/agents/<id>/agent/workshop-skills`; an
    explicit `agentDir` replaces that per-agent folder, so the skills sit in
    `<agentDir>/workshop-skills`.
    """
    custom = entry.get("agentDir") if isinstance(entry, dict) else None
    if isinstance(custom, str) and custom.strip():
        base = _agent_path(custom.strip(), home, state_dir)
    else:
        base = state_dir / "agents" / str(agent_id) / "agent"
    return base / "workshop-skills"


def _newest_mtime(path: Path) -> float | None:
    """The newest change inside one skill, or None when it cannot be read."""
    try:
        newest = path.stat().st_mtime
    except OSError:  # nexgen: allow reason="a skill folder that vanished is not counted"
        return None
    if path.is_dir():
        for child in path.rglob("*"):
            try:
                newest = max(newest, child.stat().st_mtime)
            except OSError:  # nexgen: allow reason="one unreadable file does not hide the skill"
                continue
    return newest


def _recent_skills(directory: Path, cutoff: float) -> int:
    """How many learned skills were created or changed since `cutoff`."""
    try:
        children = list(directory.iterdir())
    except OSError:  # nexgen: allow reason="no folder means no learned skills yet"
        return 0
    return sum(
        1 for child in children
        if (newest := _newest_mtime(child)) is not None and newest >= cutoff
    )


def check_workshop_skills(
    config: dict[str, Any],
    state_dir: Path,
    home: Path,
    now: float | None = None,
) -> list[CheckOutcome]:
    """One line per agent: how many skills it learned in the last 24 hours."""
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    if not entries:
        return []
    cutoff = (time.time() if now is None else now) - WORKSHOP_WINDOW_SECONDS
    outcomes: list[CheckOutcome] = []
    for agent_id in sorted(entries, key=str):
        count = _recent_skills(_workshop_dir(state_dir, home, agent_id, entries[agent_id]), cutoff)
        too_many = count > WORKSHOP_WARN_OVER
        outcomes.append(CheckOutcome(
            id=f"openclaw.workshop_skills.{agent_id}",
            severity=Severity.WARN if too_many else Severity.OK,
            message=t("Agent {agent}: {count} learned skills created or changed in the last 24 hours.",
                      agent=agent_id, count=count),
            action=(
                t("Review what {agent} learned: more than ten new procedures in a day is a lot to keep.",
                  agent=agent_id)
                if too_many else None
            ),
        ))
    return outcomes


# ------------------------------------------------------------- the Companion app


def default_wsl_distributions() -> list[str]:
    """The WSL distributions registered for this Windows user, read-only.

    The Companion's first-run setup creates one named `OpenClawGateway`; the
    registry is the only place that says so without starting WSL or a gateway.
    """
    if os.name != "nt":
        return []
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:  # nexgen: allow reason="winreg only exists on Windows"
        return []
    names: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as key:
            index = 0
            while True:
                try:
                    subkey = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(key, subkey) as distro:
                        name, _ = winreg.QueryValueEx(distro, "DistributionName")
                except OSError:  # nexgen: allow reason="one malformed distro entry is skipped"
                    continue
                if isinstance(name, str) and name:
                    names.append(name)
    except OSError:  # nexgen: allow reason="no Lxss key means WSL is not installed"
        return []
    return names


def _is_foreign_local_url(text: str) -> bool:
    """A loopback gateway address that is not the engine's port."""
    if not text.startswith(("ws://", "wss://", "http://", "https://")):
        return False
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:  # nexgen: allow reason="an unparseable URL is not evidence of a gateway"
        return False
    host = (parsed.hostname or "").casefold()
    if host not in ("localhost", "127.0.0.1", "::1"):
        return False
    return port != GATEWAY_PORT


def _gateway_urls(value: Any, *, only_gateway_keys: bool, key: str = "") -> list[str]:
    """Every gateway-looking URL string in a settings document, never printed."""
    found: list[str] = []
    if isinstance(value, str):
        if not only_gateway_keys or "gateway" in key.casefold():
            found.append(value)
    elif isinstance(value, dict):
        for name, item in value.items():
            found.extend(_gateway_urls(item, only_gateway_keys=only_gateway_keys, key=str(name)))
    elif isinstance(value, list):
        for item in value:
            found.extend(_gateway_urls(item, only_gateway_keys=only_gateway_keys, key=key))
    return found


def _companion_points_elsewhere(settings_dir: Path) -> bool:
    """Whether the Companion's own settings name a loopback gateway that is not ours.

    The Companion's settings schema is not documented field by field, so the
    check reads only what is verifiable: `settings.json` is examined for values
    under a gateway-named key, and `gateways.json` for any URL it holds.
    """
    for name, only_gateway_keys in (("settings.json", True), ("gateways.json", False)):
        path = settings_dir / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # nexgen: allow reason="a missing or unreadable settings file is not evidence of a gateway"
            continue
        if any(_is_foreign_local_url(url) for url in _gateway_urls(data, only_gateway_keys=only_gateway_keys)):
            return True
    return False


def check_companion_gateway(
    home: Path,
    env: Mapping[str, str],
    platform: str,
    wsl_distributions: Callable[[], list[str]] | None = None,
) -> CheckOutcome | None:
    """The Windows Companion may have provisioned a gateway of its own.

    Two gateways on one machine fight over the same state and port. The
    evidence is the app-owned WSL distribution, the app's WSL storage folder,
    or a gateway URL in the Companion's settings that is local but not this
    engine's loopback port.
    """
    if not platform.startswith("win"):
        return None
    local_appdata = Path(env.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    roaming = Path(env.get("APPDATA") or (home / "AppData" / "Roaming"))
    distros = (wsl_distributions or default_wsl_distributions)()
    signals: list[str] = []
    if any(str(name).casefold() == COMPANION_DISTRO.casefold() for name in distros):
        signals.append(t("the app-owned WSL distribution {name}", name=COMPANION_DISTRO))
    if (local_appdata / COMPANION_DIRNAME / "wsl").is_dir():
        signals.append(t("the app's own WSL storage"))
    if _companion_points_elsewhere(roaming / COMPANION_DIRNAME):
        signals.append(t("its settings point at another local gateway"))
    if not signals:
        return None
    return CheckOutcome(
        id="openclaw.companion_gateway",
        severity=Severity.WARN,
        message=t("The OpenClaw Companion manages a gateway of its own on this PC ({signals}): "
                  "two gateways in conflict.", signals=", ".join(signals)),
        action=t("In OpenClaw Companion open Connections and choose 'a local Gateway on this PC'."),
    )
