# nexgen: one-job reason="turning the app's files, WSL list and log end into one value about its gateway and node"
"""Where the OpenClaw Companion app keeps its gateway, read without starting it.

One job: turn the things that answer without launching the app, WSL or a gateway --
the WSL distribution list, the MSIX isolated package state, and the app's own
settings files -- into one value, so detection, the setup and the doctor never
disagree about which gateway is on this machine. Read-only by construction: no
command is run, no file is written, and nothing is started.

The app's schema is not documented field by field, so every field is optional
and a file that does not parse says nothing. The keys this engine knows are
`setupManagedDistroName`, `isLocal`, `sshTunnel`, `nativePackageFamilyName`,
`nativeRuntimeContract`, `EnableNodeMode`, and gateway URLs. A URL can carry a
token in its query string, so it is parsed for its host and its port and then
dropped. No URL, token or device key is ever returned, kept or printed, and a
`device-key*` file is never opened.

The app can also be the node of a gateway, and whether it managed to be one is
only visible in its log: the last bounded piece of that log is enough to say
whether its latest node line is the missing-credential error. The log is read
from its end in one bounded read, never opened whole.
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
from nexgen_core.openclaw.companion_isolated import (
    NATIVE_GATEWAY_CONTRACT,
    IsolatedGateway,
    read_isolated_package_state,
)

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

#: The target gateway destinations the app can point to.
TARGET_ENGINE = "engine"
TARGET_ISOLATED = "isolated"
TARGET_WSL = "wsl"
TARGET_REMOTE = "remote"
TARGET_NONE = "none"

#: `() -> names`: the WSL list, injectable so a test never reads a real registry.
Distros = Callable[[], list[str]]

#: A gateway the app names on one of these is a gateway on this PC.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: The app's log, and the tail of it this module reads. Not the whole file: the
#: app writes a health snapshot about once a minute, and the owner's log had
#: reached several megabytes with the node error the doctor has to see written
#: hours before its end.
LOG_FILENAME = "openclaw-tray.log"
LOG_TAIL_BYTES = 1024 * 1024
#: What the app writes when it has node mode on but no credential to use it.
NODE_CREDENTIAL_ERROR = "no node credential available"
#: A line saying the app's node did connect, which makes an older error stale.
NODE_CONNECTED_WORDS = ("connected", "is ready")


@dataclass(frozen=True)
class Companion:
    """What the app's own files and the WSL list say about its gateway.

    `signals` are words a person reads in the doctor and in the plan. They name
    what was found and never repeat a URL, a token or a key.
    """

    present: bool = False
    owns_local_gateway: bool = False
    points_remote: bool = False
    #: The app's settings name this engine's own gateway, on its port.
    points_here: bool = False
    #: The loopback port the app's settings name, when they name one.
    port: int | None = None
    #: The app's own node mode is switched on: this PC is meant to be a node.
    wants_node: bool = False
    #: The app's log ends on "no node credential" rather than on a connected node:
    #: node mode is on and the app cannot use it.
    node_credential_missing: bool = False
    signals: tuple[str, ...] = ()
    isolated: IsolatedGateway | None = None
    target: str = ""

    def situation(self, native_installed: bool) -> str:
        """Which of the four cases this machine is in, for the setup and the doctor."""
        if self.owns_local_gateway:
            return BOTH if native_installed else APP
        if self.points_remote:
            return APP_REMOTE
        return NATIVE if native_installed else NONE

    def names_this_gateway(self) -> bool:
        """Whether the app is set to talk to the gateway this engine configures."""
        return self.points_here and not self.owns_local_gateway

    def names_no_gateway(self) -> bool:
        """Whether the app is installed and its files name no gateway at all."""
        return self.present and not (self.owns_local_gateway or self.points_remote or self.points_here)

    @property
    def is_isolated(self) -> bool:
        """Whether the app's gateway is the isolated native MSIX gateway."""
        return self.target == TARGET_ISOLATED or (
            self.isolated is not None and self.isolated.present and self.owns_local_gateway
        )

    @property
    def is_wsl(self) -> bool:
        """Whether the app's gateway is the WSL distribution."""
        return self.target == TARGET_WSL or any(
            COMPANION_DISTRO.casefold() in s.casefold() for s in self.signals
        )

    def to_dict(self) -> dict[str, Any]:
        """The value as JSON-safe data: never a URL, a token or a device key."""
        data: dict[str, Any] = {
            "present": self.present,
            "owns_local_gateway": self.owns_local_gateway,
            "points_remote": self.points_remote,
            "points_here": self.points_here,
            "port": self.port,
            "signals": list(self.signals),
        }
        if self.wants_node:
            data["wants_node"] = True
        if self.node_credential_missing:
            data["node_credential_missing"] = True
        if self.isolated is not None:
            data["isolated"] = self.isolated.to_dict()
        if self.target:
            data["target"] = self.target
        return data


@dataclass(frozen=True)
class _Reading:
    """What one app file answered, before the two files are put together."""

    local: bool = False
    remote: bool = False
    here: bool = False
    port: int | None = None
    target: str = ""
    native_family: str = ""
    native_contract: str = ""


def _pairs(document: Any) -> Iterator[tuple[str, Any]]:
    """Every key and its value in a JSON document, at any depth."""
    if isinstance(document, dict):
        for name, item in document.items():
            yield str(name), item
            yield from _pairs(item)
    elif isinstance(document, list):
        for item in document:
            yield from _pairs(item)


def _wants_node(document: Any) -> bool:
    """Whether the app's own settings switch its node mode on: one boolean, nothing else."""
    return any(
        key.casefold() == "enablenodemode" and value is True
        for key, value in _pairs(document)
    )


def _address(text: str) -> tuple[bool, int | None] | None:
    """A gateway URL as "it is on this PC" and its port; None when unreadable."""
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


def _active_gateway_dict(document: Any) -> dict[str, Any] | None:
    """The active gateway configuration in gateways.json, if present."""
    if not isinstance(document, dict):
        return None
    active_id = document.get("activeId")
    if isinstance(active_id, str) and active_id.strip():
        aid = active_id.strip()
        gateways = document.get("gateways")
        if isinstance(gateways, list):
            for item in gateways:
                if isinstance(item, dict) and str(item.get("id")) == aid:
                    return item
        elif isinstance(gateways, dict):
            entry = gateways.get(aid)
            if isinstance(entry, dict):
                return entry
        if aid in document and isinstance(document[aid], dict):
            return document[aid]
    gateways = document.get("gateways")
    if isinstance(gateways, list) and len(gateways) == 1 and isinstance(gateways[0], dict):
        return gateways[0]
    if isinstance(gateways, dict) and len(gateways) == 1:
        entry = next(iter(gateways.values()))
        if isinstance(entry, dict):
            return entry
    entries = [v for v in document.values() if isinstance(v, dict) and any(
        k in v for k in ("url", "isLocal", "nativePackageFamilyName", "setupManagedDistroName")
    )]
    if len(entries) == 1:
        return entries[0]
    return None


def _scan_active(active: dict[str, Any], engine_port: int) -> _Reading | None:
    """Scan an explicitly identified active gateway entry."""
    if bool(active.get("sshTunnel")):
        return _Reading(remote=True, target=TARGET_REMOTE)
    url_text = active.get("url") if isinstance(active.get("url"), str) else None
    addr = _address(url_text) if url_text else None
    family = str(active.get("nativePackageFamilyName") or "").strip()
    contract = str(active.get("nativeRuntimeContract") or "").strip()
    distro = str(active.get("setupManagedDistroName") or "").strip()
    if contract == NATIVE_GATEWAY_CONTRACT or family:
        port = addr[1] if addr and addr[0] else None
        return _Reading(
            local=True, port=port, target=TARGET_ISOLATED,
            native_family=family, native_contract=contract or NATIVE_GATEWAY_CONTRACT,
        )
    if distro.casefold() == COMPANION_DISTRO.casefold():
        port = addr[1] if addr and addr[0] else None
        return _Reading(local=True, port=port, target=TARGET_WSL)
    if addr is not None:
        on_this_pc, named_port = addr
        if not on_this_pc:
            return _Reading(remote=True, target=TARGET_REMOTE)
        if named_port == engine_port:
            return _Reading(here=True, port=named_port, target=TARGET_ENGINE)
        return _Reading(local=True, port=named_port)
    return None


def _scan(document: Any, *, only_gateway_keys: bool, engine_port: int) -> _Reading:
    """What one app file says about a gateway: local, remote, which port, and here."""
    active = _active_gateway_dict(document)
    if active is not None:
        result = _scan_active(active, engine_port)
        if result is not None:
            return result
    if any(key.casefold() == "sshtunnel" and bool(value) for key, value in _pairs(document)):
        return _Reading(remote=True, target=TARGET_REMOTE)
    local = remote = here = False
    own_port = engine_seen = None
    target = native_family = native_contract = ""
    for key, value in _pairs(document):
        lowered = key.casefold()
        if lowered == "setupmanageddistroname" and isinstance(value, str) and value.strip():
            local, target = True, target or TARGET_WSL
        elif lowered == "nativepackagefamilyname" and isinstance(value, str) and value.strip():
            local, target, native_family = True, TARGET_ISOLATED, value.strip()
        elif lowered == "nativeruntimecontract" and str(value).strip() == NATIVE_GATEWAY_CONTRACT:
            local, target, native_contract = True, TARGET_ISOLATED, str(value).strip()
        elif lowered == "islocal" and isinstance(value, bool):
            local, remote = local or value, remote or not value
        elif lowered.endswith("url") and isinstance(value, str):
            if only_gateway_keys and "gateway" not in lowered:
                continue
            addr = _address(value)
            if addr is None:
                continue
            on_this_pc, named_port = addr
            if not on_this_pc:
                remote, target = True, target or TARGET_REMOTE
            elif named_port == engine_port:
                here, engine_seen, target = True, named_port, target or TARGET_ENGINE
            else:
                local, own_port = True, own_port if own_port is not None else named_port
    return _Reading(
        local=local, remote=remote, here=here,
        port=own_port if own_port is not None else engine_seen,
        target=target, native_family=native_family, native_contract=native_contract,
    )


def _json(path: Path) -> Any:
    """One app file as JSON, or None when it is absent or not readable JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a missing or malformed app file is not evidence of a gateway"
        return None


def _log_lines(path: Path, limit: int = LOG_TAIL_BYTES) -> list[str]:
    """The end of the app's log, in one bounded read: absent or unreadable means no lines."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            data = handle.read()
    except OSError:  # nexgen: allow reason="an app that wrote no log says nothing about its node"
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def log_says_no_node_credential(log_path: Path, limit: int = LOG_TAIL_BYTES) -> bool:
    """Whether the app's latest node line is the missing-credential error.

    The two facts are taken in the order they were written: an error read after a
    line saying the node connected is stale and says nothing.
    """
    last_error = last_connected = -1
    for index, line in enumerate(_log_lines(log_path, limit)):
        lowered = line.casefold()
        if NODE_CREDENTIAL_ERROR in lowered:
            last_error = index
        elif "node" in lowered and any(word in lowered for word in NODE_CONNECTED_WORDS):
            last_connected = index
    return last_error > last_connected


def engine_port() -> int:
    """The port this engine's gateway uses, imported late and read in one place."""
    from nexgen_core.openclaw.profile import GATEWAY_PORT

    return GATEWAY_PORT


def read_settings(settings_dir: Path) -> Companion:
    """What the app's own files say, with every missing field simply absent."""
    local = remote = here = False
    port = None
    present = False
    wants_node = False
    target = native_family = native_contract = ""
    for name in SETTINGS_FILES:
        document = _json(settings_dir / name)
        if document is None:
            continue
        present = True
        wants_node = wants_node or _wants_node(document)
        found = _scan(
            document, only_gateway_keys=name == "settings.json", engine_port=engine_port(),
        )
        local, remote, here = local or found.local, remote or found.remote, here or found.here
        port = port if port is not None else found.port
        target = target or found.target
        native_family = native_family or found.native_family
        native_contract = native_contract or found.native_contract
    signals: list[str] = []
    if target == TARGET_ISOLATED or native_family or native_contract:
        signals.append(t("the app's isolated native gateway"))
    elif local:
        signals.append(t("its settings name a gateway on this PC"))
    if remote:
        signals.append(t("its settings name a gateway elsewhere"))
    isolated = None
    if native_family or native_contract:
        isolated = IsolatedGateway(
            package_family_name=native_family,
            runtime_contract=native_contract or NATIVE_GATEWAY_CONTRACT,
            port=port,
        )
    return Companion(
        present=present, owns_local_gateway=local, points_remote=remote, points_here=here,
        port=port, wants_node=wants_node, signals=tuple(signals), isolated=isolated, target=target,
    )


def default_wsl_distributions() -> list[str]:
    """The WSL distributions registered for this Windows user, read-only."""
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


def _merge_isolated(
    pkg: IsolatedGateway | None,
    sett: IsolatedGateway | None,
    port: int | None,
) -> IsolatedGateway | None:
    """Merge package state files with settings entries for the isolated gateway."""
    if pkg and sett:
        return IsolatedGateway(
            package_family_name=pkg.package_family_name or sett.package_family_name,
            runtime_contract=pkg.runtime_contract or sett.runtime_contract,
            phase=pkg.phase or sett.phase,
            startup_enabled=pkg.startup_enabled if pkg.startup_enabled is not None else sett.startup_enabled,
            autostart_disabled=pkg.autostart_disabled if pkg.autostart_disabled is not None else sett.autostart_disabled,
            port=pkg.port or sett.port or port,
            process_id=pkg.process_id or sett.process_id,
        )
    return pkg or sett


def _resolve_target(
    settings: Companion,
    has_isolated: bool,
    owns_local: bool,
    distro: bool,
    storage: bool,
) -> str:
    """Which gateway target the app is pointing to."""
    if settings.target and settings.target != TARGET_NONE:
        return settings.target
    if settings.points_here:
        return TARGET_ENGINE
    if has_isolated and (owns_local or settings.present):
        return TARGET_ISOLATED
    if distro or storage:
        return TARGET_WSL
    if settings.points_remote:
        return TARGET_REMOTE
    return TARGET_NONE if settings.present else ""


def probe(
    home: Path,
    env: Mapping[str, str],
    platform: str,
    wsl_distributions: Distros | None = None,
) -> Companion:
    """The app's gateway on this machine, asked of WSL, package state, and its own files."""
    if not platform.startswith("win"):
        return Companion()
    local_appdata = Path(env.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    roaming = Path(env.get("APPDATA") or (home / "AppData" / "Roaming"))
    listed = (wsl_distributions or default_wsl_distributions)()
    distro = any(str(name).casefold() == COMPANION_DISTRO.casefold() for name in listed or [])
    storage = (local_appdata / COMPANION_DIRNAME / "wsl").is_dir()
    settings = read_settings(roaming / COMPANION_DIRNAME)

    hint = settings.isolated.package_family_name if settings.isolated else None
    pkg_isolated = read_isolated_package_state(local_appdata, hint)
    isolated = _merge_isolated(pkg_isolated, settings.isolated, settings.port)

    signals: list[str] = []
    if distro:
        signals.append(t("the app-owned WSL distribution {name}", name=COMPANION_DISTRO))
    if storage:
        signals.append(t("the app's own WSL storage"))
    has_isolated = isolated is not None and isolated.present
    if has_isolated and (settings.target == TARGET_ISOLATED or not settings.present or settings.owns_local_gateway):
        signals.append(t("the app's isolated native gateway"))
    signals.extend(settings.signals)

    seen: set[str] = set()
    unique_signals = [s for s in signals if not (s in seen or seen.add(s))]  # type: ignore[func-returns-value]
    port = settings.port if settings.port is not None else (isolated.port if isolated else None)
    owns_local = distro or storage or settings.owns_local_gateway or has_isolated
    target = _resolve_target(settings, has_isolated, owns_local, distro, storage)
    credential_missing = log_says_no_node_credential(
        local_appdata / COMPANION_DIRNAME / LOG_FILENAME,
    ) if settings.wants_node else False

    return Companion(
        present=bool(unique_signals) or settings.present or has_isolated,
        owns_local_gateway=owns_local,
        points_remote=settings.points_remote,
        points_here=settings.points_here,
        port=port,
        wants_node=settings.wants_node,
        node_credential_missing=credential_missing,
        signals=tuple(unique_signals),
        isolated=isolated,
        target=target,
    )
