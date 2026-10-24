"""The app's own gateway: read from the WSL list and its files, and never started.

Every test builds its own folders under `tmp_path` and injects the distribution
list, so no test reads the real registry, the real app folder or the real home.
The two rules that matter here are the read-only promise and the secret one: a
gateway URL can carry a token, so it is parsed for its host and port and
dropped, and a `device-key*` file is never opened at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.companion import (  # noqa: E402 - sys.path shim for cloned checkout
    APP,
    APP_REMOTE,
    BOTH,
    COMPANION_DISTRO,
    NATIVE,
    NONE,
    Companion,
    probe,
    read_settings,
)

SECRET = "super-secret-token"
DEVICE_KEY = "super-secret-device-key"


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def machine(tmp_path: Path) -> tuple[Path, dict]:
    """A fake home and the two app-data folders the app would write into."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home, {
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
    }


def app_dir(env: dict) -> Path:
    folder = Path(env["APPDATA"]) / "OpenClawTray"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write(folder: Path, name: str, body) -> Path:
    path = folder / name
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return path


def scan(env: dict, distros: list[str] | None = None) -> Companion:
    home, _ = machine(Path(env["LOCALAPPDATA"]).parent)
    return probe(home, env, "win32", wsl_distributions=lambda: list(distros or []))


# ------------------------------------------------------------ what is on this PC


def test_a_machine_without_the_app_or_the_distro_has_nothing(tmp_path):
    home, env = machine(tmp_path)

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found == Companion()
    assert found.situation(False) == NONE


def test_the_app_owned_distribution_is_a_gateway_on_this_pc(tmp_path):
    home, env = machine(tmp_path)

    found = probe(home, env, "win32", wsl_distributions=lambda: ["Ubuntu", COMPANION_DISTRO])

    assert found.present and found.owns_local_gateway and not found.points_remote
    assert any(COMPANION_DISTRO in signal for signal in found.signals)
    assert found.situation(False) == APP
    assert found.situation(True) == BOTH


def test_the_apps_own_wsl_storage_is_a_gateway_on_this_pc(tmp_path):
    home, env = machine(tmp_path)
    (Path(env["LOCALAPPDATA"]) / "OpenClawTray" / "wsl").mkdir(parents=True)

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.owns_local_gateway and found.situation(False) == APP


def test_a_gateway_url_on_the_engine_port_is_the_apps_own_connection(tmp_path):
    """The app can be pointed at this engine's gateway: that is not a second gateway."""
    home, env = machine(tmp_path)
    write(app_dir(env), "settings.json", {"GatewayUrl": "ws://127.0.0.1:18789"})

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.present and not found.owns_local_gateway
    assert found.port == 18789


def test_the_app_aimed_at_this_pc_says_so_and_is_not_a_second_gateway(tmp_path):
    """`points_here`: the person already chose the gateway this engine configures."""
    home, env = machine(tmp_path)
    write(app_dir(env), "settings.json", {"GatewayUrl": "ws://127.0.0.1:18789"})

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.points_here
    assert found.names_this_gateway()
    assert not found.names_no_gateway()
    assert found.to_dict()["points_here"] is True


def test_an_app_that_names_no_gateway_is_only_installed(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "settings.json", {"docsUrl": "https://openclaw.ai/docs"})

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.names_no_gateway()
    assert not found.names_this_gateway()


def test_an_app_with_a_gateway_of_its_own_is_never_aimed_here(tmp_path):
    aimed_here_too = Companion(present=True, owns_local_gateway=True, points_here=True, port=18789)

    assert not aimed_here_too.names_this_gateway(), "the app's own gateway is the one in force"


def test_a_loopback_url_on_another_port_is_a_gateway_of_its_own(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "gateways.json", {"draft": {"url": "ws://127.0.0.1:52190"}})

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.owns_local_gateway
    assert found.port == 52190


def test_a_connection_the_app_manages_names_its_distro(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "gateways.json", {
        "home": {"url": "ws://127.0.0.1:52190", "isLocal": True,
                 "setupManagedDistroName": COMPANION_DISTRO},
    })

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.owns_local_gateway
    assert found.port == 52190


# ------------------------------------------------ the app is connected elsewhere


def test_an_ssh_tunnel_is_a_gateway_elsewhere(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "gateways.json", {
        "vps": {"url": "ws://127.0.0.1:19000", "isLocal": False,
                "sshTunnel": {"host": "the-vps", "port": 22}},
    })

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.present and found.points_remote
    assert not found.owns_local_gateway, "a tunnel exposes a loopback port, but the gateway is not here"
    assert found.situation(False) == APP_REMOTE
    assert found.situation(True) == APP_REMOTE, "a remote app is never a conflict with the native gateway"


def test_a_remote_url_is_a_gateway_elsewhere(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "settings.json", {"gatewayUrl": "wss://gateway.example.com/socket"})

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.points_remote and not found.owns_local_gateway


def test_a_url_that_is_not_a_gateway_is_not_read(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "settings.json", {"docsUrl": "https://openclaw.ai/docs"})

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.present, "the app wrote a file, so the app is installed"
    assert not found.points_remote and not found.owns_local_gateway
    assert found.situation(False) == NONE


# ------------------------------------------------------- fields and files that lie


def test_a_file_that_does_not_parse_says_nothing(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "settings.json", "{ not json at all")

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found == Companion()


def test_fields_in_an_unexpected_shape_are_ignored(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "gateways.json", {"url": 42, "isLocal": "maybe", "sshTunnel": 0})
    write(app_dir(env), "settings.json", ["not", "an", "object"])

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    assert found.present and not found.owns_local_gateway and not found.points_remote


def test_a_missing_file_leaves_the_other_one_in_charge(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "gateways.json", {"main": {"url": "ws://localhost:52190"}})

    assert read_settings(app_dir(env)).owns_local_gateway
    assert read_settings(tmp_path / "nowhere") == Companion()


# ----------------------------------------------------------------- what is secret


def test_a_token_and_a_device_key_never_leave_the_module(tmp_path):
    home, env = machine(tmp_path)
    write(app_dir(env), "settings.json", {"GatewayUrl": f"ws://127.0.0.1:18789/?token={SECRET}"})
    key_folder = app_dir(env) / "gateways" / "main"
    key_folder.mkdir(parents=True)
    write(key_folder, "device-key-ed25519.json", {"privateKey": DEVICE_KEY})

    found = probe(home, env, "win32", wsl_distributions=lambda: [])

    printed = json.dumps(found.to_dict()) + " ".join(found.signals) + repr(found)
    assert SECRET not in printed
    assert DEVICE_KEY not in printed
    assert found.port == 18789


def test_nothing_on_disk_is_touched(tmp_path):
    home, env = machine(tmp_path)
    folder = app_dir(env)
    write(folder, "settings.json", {"GatewayUrl": "ws://127.0.0.1:18789"})
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}

    probe(home, env, "win32", wsl_distributions=lambda: [COMPANION_DISTRO])

    after = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    assert after == before


def test_the_probe_is_windows_only(tmp_path):
    home, env = machine(tmp_path)

    found = probe(home, env, "linux", wsl_distributions=lambda: [COMPANION_DISTRO])

    assert found == Companion()


# --------------------------------------------------------------- the four cases


@pytest.mark.parametrize(("local", "remote", "native", "expected"), [
    (False, False, True, NATIVE),
    (False, False, False, NONE),
    (True, False, False, APP),
    (True, False, True, BOTH),
    (False, True, False, APP_REMOTE),
    (False, True, True, APP_REMOTE),
])
def test_the_case_is_read_from_where_the_gateway_is(local, remote, native, expected):
    found = Companion(present=True, owns_local_gateway=local, points_remote=remote)

    assert found.situation(native) == expected
