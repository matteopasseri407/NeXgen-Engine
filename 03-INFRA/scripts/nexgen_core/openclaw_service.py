"""The gateway service, as OpenClaw's own CLI manages it on this machine.

One job: say where the gateway stands, and ask `openclaw gateway
install/start/stop/restart/uninstall` to change it. OpenClaw registers that
service itself on every platform -- a Scheduled Task on Windows, a systemd user
unit on Linux, a LaunchAgent on macOS -- and with it everything the hand-written
version kept getting wrong: the wait for the port, the recorded PID and process
start-time check before anything is terminated, the retry of a transient SQLite
share after a confirmed exit, and a supervisor that propagates the child's exit
result. The engine used to drive a Startup VBS, `schtasks` and its own
kill-and-poll loop beside that service, and two mechanisms registering the same
daemon is how a second gateway ended up invisible to both.

What stays here is the engine's policy: OpenClaw's status JSON read into the
shape the setup and the doctor already use, a dry run that runs nothing at all,
the Microsoft Store Companion app that fights over the same port, and a
registration an older release left behind, which is named and handed back as a
command instead of being deleted behind a person's back.

OpenClaw's CLI reads the state directory of the machine it runs on, not the one
in `home`: `home` only supplies the folders this engine inspects itself (the
Windows Startup entry and the Store package), so a copied home must never be
judged through the CLI.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from nexgen_core.openclaw_processes import (
    ProcessInfo,
    RunResult,
    default_linux_process_lister,
    default_runner,
    default_windows_process_lister,
    is_gateway_process,
)
from nexgen_core.paths import resolve_home

DEFAULT_GATEWAY_PORT = 18789
DEFAULT_RESTART_TIMEOUT_SECONDS = 600.0
#: How long `install` gets to show the gateway it may have launched before `start`.
DEFAULT_INSTALL_GRACE_SECONDS = 10.0
#: The exit code `openclaw gateway start` uses for "the service runs, the port is not ready yet".
STILL_STARTING_EXIT = 2
#: The `result` a restart carries when it activated a service and the health check did not answer.
RESTART_HEALTH_FAILED = "restart-health-failed"


def default_port_probe(port: int, host: str = "127.0.0.1") -> bool:
    """Test whether a TCP port is accepting connections on loopback."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0
    except OSError:  # nexgen: allow reason="a failed socket probe means the port is not listening"
        return False


def _payload(result: RunResult) -> dict[str, Any]:
    """The JSON object a `--json` command printed, or an empty one when it printed none."""
    text = (result.stdout or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:  # nexgen: allow reason="a CLI that printed no JSON leaves no facts, and the exit code and the error line are still reported"
        return {}
    return value if isinstance(value, dict) else {}


def _failure(result: RunResult, payload: Mapping[str, Any]) -> str | None:
    """What OpenClaw said went wrong, or None when nothing did."""
    if result.returncode == 0 and payload.get("ok") is not False:
        return None
    message = payload.get("error")
    if isinstance(message, str) and message.strip():
        return message.strip()
    lines = [line.strip() for line in f"{result.stderr or ''}\n{result.stdout or ''}".splitlines() if line.strip()]
    return lines[-1] if lines else f"openclaw exited {result.returncode}"


def _is_node_host(registration: Mapping[str, Any]) -> bool:
    """Whether an extra registration runs a node host rather than a second gateway.

    A node connects out to a gateway and opens no port here, so it does not
    compete with the service this machine runs; the CLI's deep inventory reports
    it as "another gateway-like service" all the same.
    """
    named = f"{registration.get('label') or ''} {registration.get('detail') or ''}".lower()
    return "node" in named


def removal_commands(registration: Mapping[str, Any]) -> list[str]:
    """The step a person runs to take one registration away, from OpenClaw's own detail.

    The engine prints these and never runs them: it did not create that
    registration, and one of them may be a second profile somebody meant to keep.
    """
    detail = str(registration.get("detail") or "")
    label = str(registration.get("label") or "")
    platform = str(registration.get("platform") or "")
    scope = str(registration.get("scope") or "user")
    if platform == "win32" and detail.startswith("startup: "):
        path = detail[len("startup: "):].replace("'", "''")
        return [f"Remove-Item -LiteralPath '{path}'"]
    if platform == "win32" and detail.startswith("task: "):
        name = detail[len("task: "):].split(", run:")[0].strip()
        return [f'schtasks.exe /Delete /TN "{name}" /F']
    if platform == "linux" and detail.startswith("unit backup: "):
        return [f"rm '{detail[len('unit backup: '):]}'"]
    if platform == "linux" and detail.startswith("unit: "):
        unit_scope = "--user" if scope == "user" else "--system"
        return [
            f"systemctl {unit_scope} disable --now {label}",
            f"rm '{detail[len('unit: '):]}'",
            f"systemctl {unit_scope} daemon-reload",
        ]
    if platform == "darwin" and detail.startswith("plist: "):
        domain = "system" if scope == "system" else "gui/$UID"
        return [f"launchctl bootout {domain}/{label}", f"rm '{detail[len('plist: '):]}'"]
    return []


class OpenClawService:
    """The gateway service controller: OpenClaw's CLI, read into the engine's shape."""

    def __init__(
        self,
        platform: str | None = None,
        home: Path | None = None,
        port: int = DEFAULT_GATEWAY_PORT,
        appdata: Path | None = None,
        localappdata: Path | None = None,
        runner: Callable[[list[str]], RunResult] | None = None,
        process_lister: Callable[[], list[ProcessInfo]] | None = None,
        port_probe: Callable[[int, str], bool] | None = None,
        time_provider: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        install_grace_seconds: float = DEFAULT_INSTALL_GRACE_SECONDS,
    ) -> None:
        self.platform = platform if platform is not None else sys.platform
        self.home = resolve_home(home)
        self.port = port
        self.runner = runner or default_runner
        self.port_probe = port_probe or default_port_probe
        self.time_provider = time_provider or time.monotonic
        self.sleeper = sleeper or time.sleep
        self.install_grace_seconds = install_grace_seconds

        if self.platform == "win32":
            self.appdata = appdata or Path(os.environ.get("APPDATA") or (self.home / "AppData" / "Roaming"))
            self.localappdata = localappdata or Path(os.environ.get("LOCALAPPDATA") or (self.home / "AppData" / "Local"))
            self.startup_dir = self.appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            self.process_lister = process_lister or default_windows_process_lister
        else:
            self.appdata = None
            self.localappdata = None
            self.startup_dir = None
            self.process_lister = process_lister or default_linux_process_lister

    # ----------------------------------------------------------------- the service

    def _ask(self, argv: list[str]) -> tuple[RunResult, dict[str, Any]]:
        """Run one OpenClaw command and read the JSON object it printed."""
        result = self.runner(argv)
        return result, _payload(result)

    def _facts(self) -> dict[str, Any]:
        """`openclaw gateway status --json`: the service, or nothing when it could not answer."""
        _, payload = self._ask(["openclaw", "gateway", "status", "--json"])
        return payload

    def status(self, dry_run: bool = False) -> dict[str, Any]:
        """Where the gateway stands, in the shape the setup and the doctor already read.

        `service.loaded` is OpenClaw's own word for "this machine has the service
        registered", and it is true for a registered service that is stopped as
        well: that is what `installed` means here, and why starting it is a
        separate question. A dry run asks nothing, so it reports nothing installed.
        """
        service: Mapping[str, Any] = {}
        if not dry_run:
            found = self._facts().get("service")
            service = found if isinstance(found, dict) else {}
        installed = service.get("loaded") is True
        label = service.get("label")
        gateway_procs = [p for p in self.process_lister() if is_gateway_process(p)]
        companion_present, companion_detail, companion_cmd = self._detect_companion()
        return {
            "installed": installed,
            "mechanism": str(label).lower() if installed and isinstance(label, str) and label else None,
            "process_count": len(gateway_procs),
            "processes": [{"pid": p.pid, "name": p.name, "cmdline": p.cmdline} for p in gateway_procs],
            "port_listening": self.port_probe(self.port, "127.0.0.1"),
            "companion_present": companion_present,
            "companion_detail": companion_detail,
            "companion_removal_command": companion_cmd,
            "dual_gateway": len(gateway_procs) > 1 or (bool(gateway_procs) and companion_present),
            "dry_run": dry_run,
        }

    def extra_services(self) -> list[dict[str, Any]]:
        """The gateway registrations that are not the service this machine runs.

        `status --deep` reads every Scheduled Task, every Startup entry and every
        unit and inspects what each one would start, which is the one honest way
        to tell a registration an older release or a second profile left behind
        from the service OpenClaw manages: on Windows both are called "OpenClaw
        Gateway". A node host is left out, because it opens no gateway port here.
        """
        _, payload = self._ask(["openclaw", "gateway", "status", "--deep", "--json"])
        found = payload.get("extraServices")
        if not isinstance(found, list):
            return []
        return [item for item in found if isinstance(item, dict) and not _is_node_host(item)]

    # -------------------------------------------------------------- lifecycle verbs

    def install(self, dry_run: bool = False) -> dict[str, Any]:
        """Register and start the gateway service, and read back where it landed.

        OpenClaw's install starts the service and may restart a running gateway,
        so the status that comes back is evidence of what the install did; the
        setup waits on it before deciding to start anything itself.
        """
        argv = ["openclaw", "gateway", "install", "--json"]
        ok = True
        error: str | None = None
        message: str | None = None
        if not dry_run:
            result, payload = self._ask(argv)
            ok = result.returncode == 0 and payload.get("ok") is not False
            error = _failure(result, payload)
            message = payload.get("message") if isinstance(payload.get("message"), str) else None
        return {
            "ok": ok,
            "commands": [argv],
            "status": self.status(dry_run=dry_run),
            "error": error,
            "message": message,
            "dry_run": dry_run,
        }

    def start(self, dry_run: bool = False) -> dict[str, Any]:
        """Start the gateway service and let OpenClaw check its own readiness.

        Exit code 2 is `still-starting`: the service runs and the port is not
        ready yet. That is the state the setup waits through, not a failure.
        """
        argv = ["openclaw", "gateway", "start", "--json"]
        if dry_run:
            return {"ok": True, "commands": [argv], "error": None, "message": None, "dry_run": True}
        result, payload = self._ask(argv)
        ok = result.returncode in (0, STILL_STARTING_EXIT)
        return {
            "ok": ok,
            "commands": [argv],
            "error": _failure(result, payload),
            "message": payload.get("message") if isinstance(payload.get("message"), str) else None,
            "dry_run": False,
        }

    def stop(self, dry_run: bool = False) -> dict[str, Any]:
        """Stop the gateway service, and name the pids still alive when it did not.

        `--force` is the consent OpenClaw asks for in a non-interactive shell:
        the engine stops the gateway only when a person ran a command that says
        so (`nexgen openclaw stop`, or the setup's write step), so asking again
        would be a prompt nobody can answer inside an automated run.
        """
        argv = ["openclaw", "gateway", "stop", "--json", "--force"]
        if dry_run:
            return {"ok": True, "commands": [argv], "remaining_pids": [], "error": None, "dry_run": True}
        result, payload = self._ask(argv)
        ok = result.returncode == 0 and payload.get("ok") is not False
        alive = [] if ok else self._gateway_pids()
        # What the caller needs is "no gateway process holds the file". A refusal with
        # nothing left running is a gateway that was never up: a fresh install, before
        # the service exists, gets an error from `gateway stop` and has nothing to stop.
        return {
            "ok": ok or not alive,
            "commands": [argv],
            "remaining_pids": alive,
            "error": _failure(result, payload),
            "dry_run": False,
        }

    def restart(
        self,
        wait: bool = True,
        timeout_s: float = DEFAULT_RESTART_TIMEOUT_SECONDS,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Restart the gateway service, then wait for the port the engine probes.

        A refusal never starts anything and is reported as it came: only a
        restart that activated the service and then missed its own health check
        (`restart-health-failed`) is still worth waiting for, because a cold
        start on this engine's own budget takes minutes and OpenClaw's checks
        are allowed to give up before that.
        """
        argv = ["openclaw", "gateway", "restart", "--json"]
        if dry_run:
            return {
                "ok": True, "commands": [argv], "elapsed_s": 0.0,
                "port_listening": False, "error": None, "dry_run": True,
            }
        result, payload = self._ask(argv)
        failure = _failure(result, payload)
        if failure is not None and payload.get("result") != RESTART_HEALTH_FAILED:
            return {
                "ok": False, "commands": [argv], "elapsed_s": 0.0,
                "port_listening": self.port_probe(self.port, "127.0.0.1"),
                "error": failure, "dry_run": False,
            }
        if not wait:
            ready = self.port_probe(self.port, "127.0.0.1")
            return {
                "ok": True, "commands": [argv], "elapsed_s": 0.0,
                "port_listening": ready, "error": None, "dry_run": False,
            }
        ready, elapsed = self._wait_for_port(timeout_s)
        return {
            "ok": ready,
            "commands": [argv],
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
        """Hold the gateway stopped for the block, and put it back on the way out.

        The restart happens in `finally`, so a half-written configuration can
        never leave the gateway stopped, an exception included.
        """
        stop_res = self.stop(dry_run=dry_run)
        try:
            yield stop_res
        finally:
            self.start(dry_run=dry_run)
            if wait and not dry_run:
                self._wait_for_port(timeout_s)

    def uninstall(self, dry_run: bool = False) -> dict[str, Any]:
        """Remove the service registration, leaving the configuration and state alone."""
        argv = ["openclaw", "gateway", "uninstall", "--json"]
        if dry_run:
            return {"ok": True, "commands": [argv], "error": None, "dry_run": True}
        result, payload = self._ask(argv)
        return {
            "ok": result.returncode == 0 and payload.get("ok") is not False,
            "commands": [argv],
            "error": _failure(result, payload),
            "dry_run": False,
        }

    # ---------------------------------------------------------------- the engine's half

    def _gateway_pids(self) -> list[int]:
        """The pids of every running process this module recognises as our gateway."""
        return [proc.pid for proc in self.process_lister() if is_gateway_process(proc)]

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


def extra_services(**kwargs: Any) -> list[dict[str, Any]]:
    """The gateway registrations other than the managed service, via a temporary instance."""
    return OpenClawService(**kwargs).extra_services()


def uninstall(**kwargs: Any) -> dict[str, Any]:
    """Uninstall OpenClaw gateway via a temporary service instance."""
    dry_run = kwargs.pop("dry_run", False)
    return OpenClawService(**kwargs).uninstall(dry_run=dry_run)
