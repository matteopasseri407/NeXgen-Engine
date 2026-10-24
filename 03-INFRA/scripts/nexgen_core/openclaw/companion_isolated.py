# nexgen: one-job reason="read the MSIX isolated-session gateway state and settings on Windows without launching anything"
"""Where the MSIX isolated-session gateway keeps its state, read without starting it.

One job: read the package state files under `%LOCALAPPDATA%\\Packages` and the
isolated-session contract fields in the app's gateway settings. Read-only by
construction: no token or secret is ever read or returned, no user folder is
touched, and no process is launched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The package family prefix and contract for the isolated session gateway.
NATIVE_GATEWAY_PACKAGE_PREFIX = "OpenClawFoundation.OpenClawGateway"
NATIVE_GATEWAY_CONTRACT = "isolated-session-v1"
MSIX_STATE_DIRNAME = "OpenClawGatewayMSIX"


@dataclass(frozen=True)
class IsolatedGateway:
    """The MSIX isolated-session gateway installed on Windows."""

    package_family_name: str = ""
    runtime_contract: str = ""
    phase: str = ""
    startup_enabled: bool | None = None
    autostart_disabled: bool | None = None
    port: int | None = None
    process_id: int | None = None

    @property
    def present(self) -> bool:
        return bool(self.package_family_name or self.runtime_contract or self.phase)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_family_name": self.package_family_name,
            "runtime_contract": self.runtime_contract,
            "phase": self.phase,
            "startup_enabled": self.startup_enabled,
            "autostart_disabled": self.autostart_disabled,
            "port": self.port,
            "process_id": self.process_id,
        }


def _json(path: Path) -> Any:
    """One file as JSON, or None when it is absent or malformed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a missing or malformed state file is not evidence of a gateway"
        return None


def _find_package_dir(packages: Path, family_name: str | None) -> tuple[Path | None, str]:
    """Find the LocalState/OpenClawGatewayMSIX directory under Packages."""
    if family_name:
        candidate = packages / family_name / "LocalState" / MSIX_STATE_DIRNAME
        if candidate.is_dir():
            return candidate, family_name
    try:
        for child in sorted(packages.iterdir()):
            if not child.is_dir():
                continue
            candidate = child / "LocalState" / MSIX_STATE_DIRNAME
            if candidate.is_dir():
                return candidate, child.name
            lowered = child.name.casefold()
            if "openclawfoundation.openclawgateway" in lowered or ("openclaw" in lowered and "gateway" in lowered):
                return candidate, child.name
    except OSError:  # nexgen: allow reason="an unreadable Packages folder is skipped"
        return None, ""
    return None, ""


def _read_setup(target_dir: Path) -> tuple[str, bool | None]:
    """Phase and startupEnabled from setup.json."""
    setup_file = target_dir / "setup.json"
    setup_data = _json(setup_file) if setup_file.is_file() else None
    phase = ""
    startup_enabled = None
    if isinstance(setup_data, dict):
        if isinstance(setup_data.get("phase"), str):
            phase = setup_data["phase"]
        if isinstance(setup_data.get("startupEnabled"), bool):
            startup_enabled = setup_data["startupEnabled"]
    return phase, startup_enabled


def _read_gateway(target_dir: Path) -> tuple[int | None, int | None, bool | None]:
    """Port, processId and autostartDisabled from gateway.json."""
    gw_file = target_dir / "gateway.json"
    gw_data = _json(gw_file) if gw_file.is_file() else None
    port = None
    process_id = None
    autostart_disabled = None
    if isinstance(gw_data, dict):
        observed = gw_data.get("observedPorts")
        if isinstance(observed, list) and observed and isinstance(observed[0], int):
            port = observed[0]
        elif isinstance(observed, int):
            port = observed
        if isinstance(gw_data.get("processId"), int):
            process_id = gw_data["processId"]
        if isinstance(gw_data.get("autostartDisabled"), bool):
            autostart_disabled = gw_data["autostartDisabled"]
    return port, process_id, autostart_disabled


def read_isolated_package_state(
    local_appdata: Path,
    family_name: str | None = None,
) -> IsolatedGateway | None:
    """Read the package state files in %LOCALAPPDATA%\\Packages for the isolated gateway."""
    packages = local_appdata / "Packages"
    if not packages.is_dir():
        return None
    target_dir, target_family = _find_package_dir(packages, family_name)
    if target_dir is None or not target_dir.is_dir():
        if family_name:
            return IsolatedGateway(
                package_family_name=family_name,
                runtime_contract=NATIVE_GATEWAY_CONTRACT,
            )
        return None

    phase, startup_enabled = _read_setup(target_dir)
    port, process_id, autostart_disabled = _read_gateway(target_dir)

    return IsolatedGateway(
        package_family_name=target_family,
        runtime_contract=NATIVE_GATEWAY_CONTRACT,
        phase=phase,
        startup_enabled=startup_enabled,
        autostart_disabled=autostart_disabled,
        port=port,
        process_id=process_id,
    )
