"""Avvio, arresto e stato dello stack locale.

Nessun presupposto su un server: niente firewall, niente sshd, niente utenti
remoti. Qui si assume soltanto che ci sia Docker e che la macchina sia quella
di chi sta digitando il comando.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

from nexgen_core.stack import secrets
from nexgen_core.stack.services import SERVICES, Service, compose_file, env_file

#: Un compose che non risponde entro questo tempo ha un problema che il
#: comando non può risolvere da solo, e tenerlo appeso non aiuta nessuno.
COMPOSE_TIMEOUT_SECONDS = 600

#: Il tempo che si concede a una porta per aprirsi dopo l'avvio.
PORT_TIMEOUT_SECONDS = 2.0


class StackError(RuntimeError):
    """Un problema che l'utente deve risolvere, con il rimedio nel messaggio."""


def docker_available() -> tuple[bool, str]:
    """Docker c'è ed è utilizzabile da questo utente?"""
    if shutil.which("docker") is None:
        return False, (
            "Docker non è installato su questa macchina. Installalo, oppure usa "
            "un server con 'bootstrap-vps.sh' se preferisci ospitare altrove."
        )
    probe = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if probe.returncode != 0:
        return False, (
            "Docker è installato ma non risponde a questo utente. Di solito "
            "manca l'appartenenza al gruppo docker: 'sudo usermod -aG docker $USER', "
            "poi riapri la sessione."
        )
    return True, ""


def _compose(engine_root: Path, service: Service, *args: str) -> subprocess.CompletedProcess[str]:
    cfile = compose_file(engine_root, service)
    if not cfile.is_file():
        raise StackError(f"Manca la descrizione dello stack '{service.stack}': {cfile}")
    cmd = ["docker", "compose", "--env-file", str(env_file(engine_root)), "-f", str(cfile), *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False,
        timeout=COMPOSE_TIMEOUT_SECONDS, cwd=str(cfile.parent),
    )


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    """Qualcuno è in ascolto su quella porta, adesso?"""
    try:
        with socket.create_connection((host, port), timeout=PORT_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def selected(names: list[str] | None) -> list[Service]:
    """I servizi richiesti, o tutti se non se ne chiede nessuno in particolare."""
    if not names:
        return list(SERVICES)
    chosen: list[Service] = []
    for name in names:
        match = next((s for s in SERVICES if s.name == name), None)
        if match is None:
            known = ", ".join(s.name for s in SERVICES)
            raise StackError(f"Servizio '{name}' sconosciuto. Quelli disponibili sono: {known}")
        chosen.append(match)
    return chosen


def up(engine_root: Path, names: list[str] | None = None) -> list[str]:
    """Avvia i servizi richiesti e restituisce cosa è successo."""
    ok, problem = docker_available()
    if not ok:
        raise StackError(problem)

    services = selected(names)
    actions: list[str] = []

    needed = sorted({s for service in services for s in service.requires_secret})
    generated = secrets.ensure(env_file(engine_root), needed)
    if generated:
        actions.append(f"Generati {len(generated)} segreti mancanti ({', '.join(generated)})")

    for service in services:
        res = _compose(engine_root, service, "up", "-d")
        if res.returncode != 0:
            detail = (res.stderr or res.stdout).strip().splitlines()
            raise StackError(
                f"'{service.name}' non è partito: {detail[-1] if detail else 'nessun dettaglio'}"
            )
        actions.append(f"{service.name} avviato")

    return actions


def down(engine_root: Path, names: list[str] | None = None) -> list[str]:
    """Ferma i servizi, senza toccare i loro dati."""
    ok, problem = docker_available()
    if not ok:
        raise StackError(problem)

    actions: list[str] = []
    for service in selected(names):
        res = _compose(engine_root, service, "stop")
        if res.returncode != 0:
            detail = (res.stderr or res.stdout).strip().splitlines()
            actions.append(f"{service.name}: non si è fermato ({detail[-1] if detail else '?'})")
        else:
            actions.append(f"{service.name} fermato")
    return actions


def status(engine_root: Path) -> list[tuple[str, bool, str]]:
    """Per ogni servizio: risponde, e su quale porta."""
    out: list[tuple[str, bool, str]] = []
    for service in SERVICES:
        alive = port_is_open(service.port)
        out.append((service.name, alive, f"127.0.0.1:{service.port}"))
    return out


def logs(engine_root: Path, name: str, lines: int = 50) -> str:
    """Le ultime righe di un servizio, per capire perché non parte."""
    service = next((s for s in SERVICES if s.name == name), None)
    if service is None:
        raise StackError(f"Servizio '{name}' sconosciuto.")
    res = _compose(engine_root, service, "logs", "--tail", str(lines))
    return res.stdout or res.stderr
