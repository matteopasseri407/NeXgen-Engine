"""The systemd user bus of a session entered with `sudo -iu`.

`sudo -iu <account>` starts a login shell with none of the variables a desktop
session exports, so `systemctl --user` cannot reach the bus of the very user the
installer runs as, and the server role stopped at step 1/9 with "The systemd user
bus is not available in this session" on a machine where linger was on and the
bus was there. The installer now points itself at that user's own bus first.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import host_preflight, setup_steps  # noqa: E402
from nexgen_core.openclaw_service import RunResult  # noqa: E402

#: The session bus and its socket are POSIX; a Windows runner has neither.
_NO_SESSION_BUS = os.name == "nt" or not hasattr(socket, "AF_UNIX")


def _bus_socket(root: Path, uid: int) -> Path:
    """A fake /run/user tree with this user's bus socket in it."""
    directory = root / str(uid)
    directory.mkdir(parents=True)
    bus = directory / "bus"
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(bus))
    finally:
        sock.close()
    return bus


@pytest.mark.skipif(_NO_SESSION_BUS, reason="the session bus and its socket are POSIX")
def test_a_session_without_variables_is_pointed_at_its_own_bus(tmp_path: Path) -> None:
    base = tmp_path / "run"
    uid = os.getuid()
    bus = _bus_socket(base, uid)

    values = host_preflight.session_bus_values({}, base=base)

    assert values["XDG_RUNTIME_DIR"] == str(bus.parent)
    assert values["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={bus}"


@pytest.mark.skipif(_NO_SESSION_BUS, reason="the session bus and its socket are POSIX")
def test_nothing_is_changed_when_the_session_already_names_a_bus(tmp_path: Path) -> None:
    base = tmp_path / "run"
    _bus_socket(base, os.getuid())

    assert host_preflight.session_bus_values({"XDG_RUNTIME_DIR": "/run/user/1"}, base=base) == {}
    assert host_preflight.session_bus_values({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/b"}, base=base) == {}


@pytest.mark.skipif(_NO_SESSION_BUS, reason="the session bus and its socket are POSIX")
def test_nothing_is_invented_when_the_bus_socket_is_not_there(tmp_path: Path) -> None:
    base = tmp_path / "run"
    (base / str(os.getuid())).mkdir(parents=True)

    assert host_preflight.session_bus_values({}, base=base) == {}


@pytest.mark.skipif(_NO_SESSION_BUS, reason="the session bus and its socket are POSIX")
def test_the_linux_host_check_adopts_the_bus_before_asking_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    uid = os.getuid()
    base = tmp_path / "run"
    bus = _bus_socket(base, uid)
    monkeypatch.setattr(host_preflight, "RUNTIME_DIR_ROOT", base)
    setup = SimpleNamespace(
        platform="linux", dry_run=False, env={}, out=lambda _line: None,
        run=lambda _argv, _env=None: RunResult(0, "", ""),
    )

    assert setup_steps._check_linux_host(setup, None, False) is True

    assert setup.env["XDG_RUNTIME_DIR"] == str(bus.parent)
    assert setup.env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={bus}"
