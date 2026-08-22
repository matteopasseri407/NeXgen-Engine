"""Starting, stopping, and checking the status of the local stack.

No assumptions about a server: no firewall, no sshd, no remote users. Here
the only assumption is that Docker is present and that the machine is the
one the person typing the command is sitting at.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.stack import secrets
from nexgen_core.stack.services import SERVICES, Service, compose_file, env_file

#: A compose that doesn't respond within this time has a problem the
#: command can't solve on its own, and leaving it hanging helps no one.
COMPOSE_TIMEOUT_SECONDS = 600

#: The time given to a port to open after startup.
PORT_TIMEOUT_SECONDS = 2.0


class StackError(RuntimeError):
    """A problem the user must resolve, with the remedy in the message."""


def docker_available() -> tuple[bool, str]:
    """Is Docker present and usable by this user?"""
    if shutil.which("docker") is None:
        return False, t(
            "Docker is not installed on this machine. Install it, or use a server with "
            "'bootstrap-vps.sh' if you prefer to host it elsewhere."
        )
    probe = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if probe.returncode != 0:
        return False, t(
            "Docker is installed but does not answer this user. Usually the missing piece is "
            "group membership: 'sudo usermod -aG docker $USER', then open a new session."
        )
    return True, ""


def _compose(engine_root: Path, service: Service, *args: str) -> subprocess.CompletedProcess[str]:
    cfile = compose_file(engine_root, service)
    if not cfile.is_file():
        raise StackError(t("Missing stack description for '{stack}': {cfile}", stack=service.stack, cfile=cfile))
    cmd = ["docker", "compose", "--env-file", str(env_file(engine_root)), "-f", str(cfile), *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False,
        timeout=COMPOSE_TIMEOUT_SECONDS, cwd=str(cfile.parent),
    )


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    """Is anyone listening on that port, right now?"""
    try:
        with socket.create_connection((host, port), timeout=PORT_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def selected(names: list[str] | None) -> list[Service]:
    """The requested services, or all of them if none in particular is asked for."""
    if not names:
        return list(SERVICES)
    chosen: list[Service] = []
    for name in names:
        match = next((s for s in SERVICES if s.name == name), None)
        if match is None:
            known = ", ".join(s.name for s in SERVICES)
            raise StackError(t("Unknown service '{name}'. Available ones are: {known}", name=name, known=known))
        chosen.append(match)
    return chosen


def up(engine_root: Path, names: list[str] | None = None) -> list[str]:
    """Starts the requested services and returns what happened."""
    ok, problem = docker_available()
    if not ok:
        raise StackError(problem)

    services = selected(names)
    actions: list[str] = []

    needed = sorted({s for service in services for s in service.requires_secret})
    generated = secrets.ensure(env_file(engine_root), needed)
    if generated:
        actions.append(t(
            "Generated {count} missing secrets ({names})",
            count=len(generated), names=", ".join(generated),
        ))

    for service in services:
        res = _compose(engine_root, service, "up", "-d")
        if res.returncode != 0:
            detail = (res.stderr or res.stdout).strip().splitlines()
            raise StackError(t(
                "'{name}' did not start: {detail}",
                name=service.name, detail=detail[-1] if detail else t("no detail"),
            ))
        actions.append(t("{name} started", name=service.name))

    return actions


def down(engine_root: Path, names: list[str] | None = None) -> list[str]:
    """Stops the services, without touching their data."""
    ok, problem = docker_available()
    if not ok:
        raise StackError(problem)

    actions: list[str] = []
    for service in selected(names):
        res = _compose(engine_root, service, "stop")
        if res.returncode != 0:
            detail = (res.stderr or res.stdout).strip().splitlines()
            actions.append(t(
                "{name}: did not stop ({detail})",
                name=service.name, detail=detail[-1] if detail else "?",
            ))
        else:
            actions.append(t("{name} stopped", name=service.name))
    return actions


def status(engine_root: Path) -> list[tuple[str, bool, str]]:
    """For each service: whether it responds, and on which port."""
    out: list[tuple[str, bool, str]] = []
    for service in SERVICES:
        alive = port_is_open(service.port)
        out.append((service.name, alive, f"127.0.0.1:{service.port}"))
    return out


def logs(engine_root: Path, name: str, lines: int = 50) -> str:
    """The last lines of a service, to understand why it won't start."""
    service = next((s for s in SERVICES if s.name == name), None)
    if service is None:
        raise StackError(t("Unknown service '{name}'.", name=name))
    res = _compose(engine_root, service, "logs", "--tail", str(lines))
    return res.stdout or res.stderr
