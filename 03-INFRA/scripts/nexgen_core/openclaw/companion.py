"""Where the OpenClaw Companion app keeps its gateway, read without starting it.

One job: turn the two things that answer without launching the app, WSL or a
gateway -- the WSL distribution list and the app's own settings files -- into one
value, so detection, the setup and the doctor never disagree about which gateway
is on this machine. Read-only by construction: no command is run, no file is
written, and nothing is started.

The app's schema is not documented field by field, so every field is optional
and a file that does not parse says nothing. The keys this engine knows are
`setupManagedDistroName`, `isLocal`, `sshTunnel` and a gateway URL; a URL can
carry a token in its query string, so it is parsed for its host and its port and
then dropped. No URL, token or device key is ever returned, kept or printed, and
a `device-key*` file is never opened.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nexgen_core.i18n import t

#: The distribution the app provisions for its own gateway, and the two folders
#: it keeps its settings and that distribution in.
COMPANION_DISTRO = "OpenClawGateway"
COMPANION_DIRNAME = "OpenClawTray"
#: The app's own files: the connection in use, and every connection it knows.
SETTINGS_FILES = ("settings.json", "gateways.json")

#: The four situations the engine tells apart, plus a machine with neither.
NONE = "none"
NATIVE = "native"
APP = "app"
BOTH = "both"
APP_REMOTE = "app_remote"

#: `() -> names`: the WSL list, injectable so a test never reads a real registry.
Distros = Callable[[], list[str]]

#: A gateway the app names on one of these is a gateway on this PC.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class Companion:
    """What the app's own files and the WSL list say about its gateway.

    `signals` are words a person reads in the doctor and in the plan. They name
    what was found and never repeat a URL, a token or a key.
    """

    present: bool = False
    owns_local_gateway: bool = False
    points_remote: bool = False
    #: The loopback port the app's settings name, when they name one.
    port: int | None = None
    signals: tuple[str, ...] = ()

    def situation(self, native_installed: bool) -> str:
        """Which of the four cases this machine is in, for the setup and the doctor."""
        if self.owns_local_gateway:
            return BOTH if native_installed else APP
        if self.points_remote:
            return APP_REMOTE
        return NATIVE if native_installed else NONE

    def to_dict(self) -> dict[str, Any]:
        """The value as JSON-safe data: never a URL, a token or a device key."""
        return {
            "present": self.present,
            "owns_local_gateway": self.owns_local_gateway,
            "points_remote": self.points_remote,
            "port": self.port,
            "signals": list(self.signals),
        }


def _pairs(document: Any) -> Iterator[tuple[str, Any]]:
    """Every key and its value in a JSON document, at any depth."""
    if isinstance(document, dict):
        for name, item in document.items():
            yield str(name), item
            yield from _pairs(item)
    elif isinstance(document, list):
        for item in document:
            yield from _pairs(item)


def _address(text: str) -> tuple[bool, int | None] | None:
    """A gateway URL as "it is on this PC" and its port; None when unreadable.

    The whole URL is dropped here: a gateway URL may carry `?token=` and only
    the host and the port are answers this module has any use for.
    """
    if not text.startswith(("ws://", "wss://", "http://", "https://")):
        return None
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:  # nexgen: allow reason="an unparseable URL is not evidence of a gateway"
        return None
    host = (parsed.hostname or "").casefold()
    if not host:
        return None
    return host in LOCAL_HOSTS, port


def _scan(document: Any, *, only_gateway_keys: bool) -> tuple[bool, bool, int | None]:
    """What one app file says about a gateway: local, remote, and which port.

    `sshTunnel` wins over everything else: a tunnel forwards to a gateway on
    another machine, so whatever address it exposes on this PC says nothing
    about where that gateway is. `isLocal` is the app's own word for the
    connection. `only_gateway_keys` is for `settings.json`, which holds other
    URLs too: there, only a key with "gateway" in its name is read.
    """
    if any(key.casefold() == "sshtunnel" and bool(value) for key, value in _pairs(document)):
        return False, True, None
    local = remote = False
    port: int | None = None
    for key, value in _pairs(document):
        lowered = key.casefold()
        if lowered == "setupmanageddistroname" and isinstance(value, str) and value.strip():
            local = True
        elif lowered == "islocal" and isinstance(value, bool):
            local, remote = local or value, remote or not value
        elif lowered.endswith("url") and isinstance(value, str):
            if only_gateway_keys and "gateway" not in lowered:
                continue
            address = _address(value)
            if address is None:
                continue
            on_this_pc, named_port = address
            local = local or on_this_pc
            remote = remote or not on_this_pc
            if port is None and on_this_pc:
                port = named_port
    return local, remote, port


def _json(path: Path) -> Any:
    """One app file as JSON, or None when it is absent or not readable JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a missing or malformed app file is not evidence of a gateway"
        return None


def read_settings(settings_dir: Path) -> Companion:
    """What the app's own files say, with every missing field simply absent."""
    local = remote = False
    port: int | None = None
    present = False
    for name in SETTINGS_FILES:
        document = _json(settings_dir / name)
        if document is None:
            continue
        present = True
        found_local, found_remote, found_port = _scan(document, only_gateway_keys=name == "settings.json")
        local, remote = local or found_local, remote or found_remote
        if port is None:
            port = found_port
    signals: list[str] = []
    if local:
        signals.append(t("its settings name a gateway on this PC"))
    if remote:
        signals.append(t("its settings name a gateway elsewhere"))
    return Companion(
        present=present, owns_local_gateway=local, points_remote=remote,
        port=port, signals=tuple(signals),
    )


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


def probe(
    home: Path,
    env: Mapping[str, str],
    platform: str,
    wsl_distributions: Distros | None = None,
) -> Companion:
    """The app's gateway on this machine, asked of the WSL list and its own files.

    Nothing is started: the registry answers for the distributions without
    booting WSL, and the app's files are read as JSON. On a machine that is not
    Windows there is nothing to find, and this costs one string comparison.
    """
    if not platform.startswith("win"):
        return Companion()
    local_appdata = Path(env.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    roaming = Path(env.get("APPDATA") or (home / "AppData" / "Roaming"))
    listed = (wsl_distributions or default_wsl_distributions)()
    distro = any(str(name).casefold() == COMPANION_DISTRO.casefold() for name in listed or [])
    storage = (local_appdata / COMPANION_DIRNAME / "wsl").is_dir()
    settings = read_settings(roaming / COMPANION_DIRNAME)
    signals: list[str] = []
    if distro:
        signals.append(t("the app-owned WSL distribution {name}", name=COMPANION_DISTRO))
    if storage:
        signals.append(t("the app's own WSL storage"))
    signals.extend(settings.signals)
    return Companion(
        present=bool(signals) or settings.present,
        owns_local_gateway=distro or storage or settings.owns_local_gateway,
        points_remote=settings.points_remote,
        port=settings.port,
        signals=tuple(signals),
    )
