"""OpenClaw gateway service lifecycle management.

Handles installation, status inspection, graceful stop, start, restart,
and uninstallation of the OpenClaw gateway daemon across Windows and Linux.
Provides context-manager access for safe configuration edits and detects
duplicate processes or Microsoft Store Companion app conflicts.
"""
from __future__ import annotations

import contextlib
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from nexgen_core.openclaw_processes import (
    ProcessInfo,
    RunResult,
    default_linux_process_lister,
    default_runner,
    default_windows_process_lister,
    is_gateway_process,
)
from nexgen_core.paths import openclaw_config, resolve_home
from nexgen_core.processes import spawn_detached

DEFAULT_GATEWAY_PORT = 18789
DEFAULT_RESTART_TIMEOUT_SECONDS = 600.0
#: How long a killed gateway gets to let go of its port before `start` would race it.
DEFAULT_STOP_TIMEOUT_SECONDS = 60.0
#: How long `install` gets to show the gateway it may have launched before `start`.
DEFAULT_INSTALL_GRACE_SECONDS = 10.0


def default_port_probe(port: int, host: str = "127.0.0.1") -> bool:
    """Test whether a TCP port is accepting connections on loopback."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0
    except OSError:  # nexgen: allow reason="a failed socket probe means the port is not listening"
        return False


class OpenClawService:
    """Service controller for the OpenClaw gateway daemon."""

    def __init__(
        self,
        platform: str | None = None,
        home: Path | None = None,
        state_dir: Path | None = None,
        appdata: Path | None = None,
        localappdata: Path | None = None,
        systemd_user_dir: Path | None = None,
        port: int = DEFAULT_GATEWAY_PORT,
        runner: Callable[[list[str]], RunResult] | None = None,
        spawner: Callable[[list[str]], Any] | None = None,
        process_lister: Callable[[], list[ProcessInfo]] | None = None,
        port_probe: Callable[[int, str], bool] | None = None,
        time_provider: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        install_grace_seconds: float = DEFAULT_INSTALL_GRACE_SECONDS,
    ) -> None:
        self.platform = platform if platform is not None else sys.platform
        self.home = resolve_home(home)
        self.state_dir = state_dir or openclaw_config(self.home).parent
        self.port = port
        self.runner = runner or default_runner
        self.spawn = spawner or spawn_detached
        self.port_probe = port_probe or default_port_probe
        self.install_grace_seconds = install_grace_seconds
        self.time_provider = time_provider or time.monotonic
        self.sleeper = sleeper or time.sleep

        if self.platform == "win32":
            self.appdata = appdata or Path(os.environ.get("APPDATA") or (self.home / "AppData" / "Roaming"))
            self.localappdata = localappdata or Path(os.environ.get("LOCALAPPDATA") or (self.home / "AppData" / "Local"))
            self.startup_dir = self.appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            self.systemd_user_dir = None
            self.process_lister = process_lister or default_windows_process_lister
        else:
            self.appdata = None
            self.localappdata = None
            self.startup_dir = None
            self.systemd_user_dir = systemd_user_dir or (self.home / ".config" / "systemd" / "user")
            self.process_lister = process_lister or default_linux_process_lister

    def _detect_mechanism(self, dry_run: bool = False) -> str | None:
        """Detect whether and how the gateway is registered as an autostart service."""
        if self.platform == "win32":
            # Check for VBS file in Startup folder first
            if self.startup_dir and (self.startup_dir / "OpenClaw Gateway.vbs").exists():
                return "vbs"
            # Check for Windows Scheduled Task if not in dry-run mode
            if not dry_run:
                check_schtasks = self.runner(["schtasks.exe", "/Query", "/TN", "OpenClaw Gateway"])
                if check_schtasks.returncode == 0:
                    return "schtasks"
            return None
        # Linux: check for systemd user unit
        if self.systemd_user_dir and (self.systemd_user_dir / "openclaw-gateway.service").exists():
            return "systemd"
        return None

    def _detect_companion(self) -> tuple[bool, str | None, str | None]:
        """Detect conflicting Microsoft Store OpenClaw Companion application."""
        if self.platform != "win32" or not self.localappdata:
            return False, None, None
        packages_dir = self.localappdata / "Packages"
        found_pkg = None
        if packages_dir.is_dir():
            for entry in packages_dir.iterdir():
                if "openclaw" in entry.name.lower():
                    found_pkg = entry.name
                    break
        found_lnk = None
        if self.startup_dir:
            lnk = self.startup_dir / "OpenClaw Companion.lnk"
            if lnk.exists():
                found_lnk = str(lnk)

        if found_pkg or found_lnk:
            detail = f"Package: {found_pkg or 'none'}, Shortcut: {found_lnk or 'none'}"
            removal_cmd = "Get-AppxPackage *OpenClaw* | Remove-AppxPackage"
            return True, detail, removal_cmd
        return False, None, None

    def status(self, dry_run: bool = False) -> dict[str, Any]:
        """Inspect the current status of the OpenClaw gateway daemon."""
        mechanism = self._detect_mechanism(dry_run=dry_run)
        all_procs = self.process_lister()
        gateway_procs = [p for p in all_procs if is_gateway_process(p)]
        listening = self.port_probe(self.port, "127.0.0.1")
        companion_present, companion_detail, companion_cmd = self._detect_companion()

        process_count = len(gateway_procs)
        dual_gateway = process_count > 1 or (process_count >= 1 and companion_present)

        return {
            "installed": mechanism is not None,
            "mechanism": mechanism,
            "process_count": process_count,
            "processes": [{"pid": p.pid, "name": p.name, "cmdline": p.cmdline} for p in gateway_procs],
            "port_listening": listening,
            "companion_present": companion_present,
            "companion_detail": companion_detail,
            "companion_removal_command": companion_cmd,
            "dual_gateway": dual_gateway,
            "dry_run": dry_run,
        }

    def install(self, dry_run: bool = False) -> dict[str, Any]:
        """Install the gateway as a background service via openclaw gateway install."""
        cmd = ["openclaw", "gateway", "install"]
        commands = [cmd]
        ok = True
        if not dry_run:
            res = self.runner(cmd)
            ok = res.returncode == 0
        current_status = self.status(dry_run=dry_run)
        return {
            "ok": ok,
            "commands": commands,
            "status": current_status,
            "dry_run": dry_run,
        }

    def stop(self, dry_run: bool = False) -> dict[str, Any]:
        """Terminate all running gateway processes or stop the systemd service."""
        commands: list[list[str]] = []
        stopped_pids: list[int] = []

        if self.platform == "linux":
            cmd = ["systemctl", "--user", "stop", "openclaw-gateway.service"]
            commands.append(cmd)
            if not dry_run:
                self.runner(cmd)
        else:
            all_procs = self.process_lister()
            gateway_procs = [p for p in all_procs if is_gateway_process(p)]
            for proc in gateway_procs:
                cmd = ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"]
                commands.append(cmd)
                stopped_pids.append(proc.pid)
                if not dry_run:
                    self.runner(cmd)

        remaining: list[int] = []
        if not dry_run:
            remaining = self._wait_until_stopped(DEFAULT_STOP_TIMEOUT_SECONDS)
        return {
            "ok": not remaining,
            "commands": commands,
            "stopped_pids": stopped_pids,
            "remaining_pids": remaining,
            "dry_run": dry_run,
        }

    def _wait_until_stopped(self, timeout_s: float) -> list[int]:
        """Waits until no gateway process is left and the port is free.

        `taskkill` returns before Windows has torn the process down: starting the
        new gateway at once lets it find the port still held, and that race is
        how two gateways ended up running side by side. Returns the pids still
        alive at the deadline (an empty list means stopped).
        """
        deadline = self.time_provider() + max(0.0, timeout_s)
        while True:
            alive = [p.pid for p in self.process_lister() if is_gateway_process(p)]
            if not alive and not self.port_probe(self.port, "127.0.0.1"):
                return []
            if self.time_provider() >= deadline:
                return alive
            self.sleeper(0.5)

    def start(self, dry_run: bool = False) -> dict[str, Any]:
        """Launch the gateway service using the detected platform mechanism."""
        commands: list[list[str]] = []
        mechanism = self._detect_mechanism(dry_run=dry_run)

        if self.platform == "linux":
            cmd = ["systemctl", "--user", "start", "openclaw-gateway.service"]
            commands.append(cmd)
            if not dry_run:
                self.runner(cmd)
        else:
            if mechanism == "schtasks":
                cmd = ["schtasks.exe", "/Run", "/TN", "OpenClaw Gateway"]
                commands.append(cmd)
                if not dry_run:
                    self.spawn(cmd)
            else:
                vbs = (self.startup_dir / "OpenClaw Gateway.vbs") if self.startup_dir else None
                cmd = ["wscript.exe", str(vbs)] if vbs and vbs.is_file() else ["openclaw", "gateway", "start"]
                commands.append(cmd)
                if not dry_run:
                    self.spawn(cmd)

        return {
            "ok": True,
            "commands": commands,
            "mechanism": mechanism,
            "dry_run": dry_run,
        }

    def _wait_for_port(self, timeout_s: float) -> tuple[bool, float]:
        """Poll loop waiting for the gateway port to become available."""
        start_time = self.time_provider()
        deadline = start_time + max(0.0, timeout_s)
        while True:
            if self.port_probe(self.port, "127.0.0.1"):
                return True, self.time_provider() - start_time
            if self.time_provider() >= deadline:
                return False, self.time_provider() - start_time
            self.sleeper(min(1.0, max(0.1, deadline - self.time_provider())))

    def restart(
        self,
        wait: bool = True,
        timeout_s: float = DEFAULT_RESTART_TIMEOUT_SECONDS,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Restart the gateway daemon, waiting for the port to respond if requested."""
        commands: list[list[str]] = []

        if self.platform == "linux":
            cmd = ["systemctl", "--user", "restart", "openclaw-gateway.service"]
            commands.append(cmd)
            if not dry_run:
                self.runner(cmd)
        else:
            stop_res = self.stop(dry_run=dry_run)
            commands.extend(stop_res["commands"])
            if not stop_res["ok"]:
                return {
                    "ok": False,
                    "commands": commands,
                    "elapsed_s": 0.0,
                    "port_listening": self.port_probe(self.port, "127.0.0.1"),
                    "error": f"Gateway processes still alive after stop: {stop_res['remaining_pids']}",
                    "dry_run": dry_run,
                }
            start_res = self.start(dry_run=dry_run)
            commands.extend(start_res["commands"])

        if dry_run or not wait:
            return {
                "ok": True,
                "commands": commands,
                "elapsed_s": 0.0,
                "port_listening": False if dry_run else self.port_probe(self.port, "127.0.0.1"),
                "dry_run": dry_run,
            }

        ready, elapsed = self._wait_for_port(timeout_s)
        return {
            "ok": ready,
            "commands": commands,
            "elapsed_s": elapsed,
            "port_listening": ready,
            "error": None if ready else f"Timed out waiting for port {self.port} after {elapsed:.1f}s",
            "dry_run": False,
        }

    @contextlib.contextmanager
    def stopped(
        self,
        wait: bool = True,
        timeout_s: float = DEFAULT_RESTART_TIMEOUT_SECONDS,
        dry_run: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Context manager keeping the gateway stopped during critical operations."""
        stop_res = self.stop(dry_run=dry_run)
        try:
            yield stop_res
        finally:
            self.start(dry_run=dry_run)
            if wait and not dry_run:
                self._wait_for_port(timeout_s)

    def uninstall(self, dry_run: bool = False) -> dict[str, Any]:
        """Stop and remove the service configuration without altering openclaw.json."""
        commands: list[list[str]] = []
        stop_res = self.stop(dry_run=dry_run)
        commands.extend(stop_res["commands"])

        uninst_cmd = ["openclaw", "gateway", "uninstall"]
        commands.append(uninst_cmd)
        if not dry_run:
            self.runner(uninst_cmd)

        if self.platform == "win32":
            if self.startup_dir:
                vbs = self.startup_dir / "OpenClaw Gateway.vbs"
                if vbs.exists():
                    commands.append(["remove_file", str(vbs)])
                    if not dry_run:
                        vbs.unlink(missing_ok=True)
            del_task = ["schtasks.exe", "/Delete", "/TN", "OpenClaw Gateway", "/F"]
            commands.append(del_task)
            if not dry_run:
                self.runner(del_task)
        else:
            if self.systemd_user_dir:
                unit = self.systemd_user_dir / "openclaw-gateway.service"
                if unit.exists():
                    commands.append(["remove_file", str(unit)])
                    if not dry_run:
                        unit.unlink(missing_ok=True)
            reload_cmd = ["systemctl", "--user", "daemon-reload"]
            commands.append(reload_cmd)
            if not dry_run:
                self.runner(reload_cmd)

        return {
            "ok": True,
            "commands": commands,
            "dry_run": dry_run,
        }


def status(**kwargs: Any) -> dict[str, Any]:
    """Inspect OpenClaw gateway status via a temporary service instance."""
    dry_run = kwargs.pop("dry_run", False)
    return OpenClawService(**kwargs).status(dry_run=dry_run)


def install(**kwargs: Any) -> dict[str, Any]:
    """Install OpenClaw gateway via a temporary service instance."""
    dry_run = kwargs.pop("dry_run", False)
    return OpenClawService(**kwargs).install(dry_run=dry_run)


def stop(**kwargs: Any) -> dict[str, Any]:
    """Stop OpenClaw gateway via a temporary service instance."""
    dry_run = kwargs.pop("dry_run", False)
    return OpenClawService(**kwargs).stop(dry_run=dry_run)


def start(**kwargs: Any) -> dict[str, Any]:
    """Start OpenClaw gateway via a temporary service instance."""
    dry_run = kwargs.pop("dry_run", False)
    return OpenClawService(**kwargs).start(dry_run=dry_run)


def restart(
    wait: bool = True,
    timeout_s: float = DEFAULT_RESTART_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> dict[str, Any]:
    """Restart OpenClaw gateway via a temporary service instance."""
    dry_run = kwargs.pop("dry_run", False)
    return OpenClawService(**kwargs).restart(wait=wait, timeout_s=timeout_s, dry_run=dry_run)


@contextlib.contextmanager
def stopped(
    wait: bool = True,
    timeout_s: float = DEFAULT_RESTART_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> Iterator[dict[str, Any]]:
    """Context manager holding OpenClaw gateway stopped."""
    dry_run = kwargs.pop("dry_run", False)
    svc = OpenClawService(**kwargs)
    with svc.stopped(wait=wait, timeout_s=timeout_s, dry_run=dry_run) as res:
        yield res


def uninstall(**kwargs: Any) -> dict[str, Any]:
    """Uninstall OpenClaw gateway via a temporary service instance."""
    dry_run = kwargs.pop("dry_run", False)
    return OpenClawService(**kwargs).uninstall(dry_run=dry_run)
