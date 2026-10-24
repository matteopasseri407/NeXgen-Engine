"""The gateway probe belongs to the machine, not to a copy of it.

There is one OpenClaw gateway per machine, on a loopback port. A copy of the
home running beside the working installation (which is what `NEXGEN_HOME` is
for, and what every test builds) used to open a connection to that port anyway:
the copy reported the health of a process it does not own, and a test touched
the machine it was supposed to leave alone.

The check is not removed: on the home the machine really uses, it still runs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import paths  # noqa: E402
from nexgen_core.openclaw import doctor as openclaw_doctor  # noqa: E402
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402

CONFIG = {
    "gateway": {"port": 18789, "mode": "local"},
    "agents": {"ownership": "explicit", "entries": {"main": {"model": "opencode-go/deepseek-v4.1-flash"}}},
}


def _copy_home(tmp_path: Path) -> Path:
    home = tmp_path / "copy"
    config = home / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(CONFIG), encoding="utf-8")
    return home


def _snapshot(home: Path) -> Detection:
    """A snapshot with no probe and no command behind it: nothing leaves the test."""
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6",
                              config_path=str(home / ".openclaw" / "openclaw.json"), config_present=True),
        claude=Login(binary=False, login=False),
        codex=Login(binary=False, login=False),
        opencode_go=False,
        local=LocalModels(),
        n8n=False,
    )


def _collect(home: Path, **kwargs) -> dict:
    outcomes = openclaw_doctor.collect(
        detected=_snapshot(home),
        home=home,
        vault_data=home.parent / "vault",
        engine_root=home / "engine",
        env={"LOCALAPPDATA": str(home / "localappdata")},
        platform="linux",
        node_version=lambda: "v24.22.1",
        linger_dir=home / "no-linger",
        **kwargs,
    )
    return {outcome.id: outcome for outcome in outcomes}


def test_a_copy_never_opens_a_connection_to_the_machines_gateway(tmp_path, monkeypatch):
    home = _copy_home(tmp_path)
    opened: list[tuple[str, int, float]] = []

    def recorded(host: str, port: int, timeout: float) -> bool:
        opened.append((host, port, timeout))
        return True

    monkeypatch.setattr(openclaw_doctor.doctor_runtime, "default_connect", recorded)

    listening = _collect(home)["openclaw.gateway.listening"]

    assert opened == [], "una copia non deve sondare il gateway della macchina"
    assert listening.severity.value == "ok"
    assert "18789" in listening.message
    assert listening.message == openclaw_doctor.doctor_runtime.check_listening_of_a_copy(CONFIG).message


def test_the_machines_own_home_still_gets_the_probe(tmp_path, monkeypatch):
    home = _copy_home(tmp_path)
    opened: list[tuple[str, int, float]] = []

    def recorded(host: str, port: int, timeout: float) -> bool:
        opened.append((host, port, timeout))
        return False

    monkeypatch.setattr(openclaw_doctor.doctor_runtime, "default_connect", recorded)
    monkeypatch.setattr(openclaw_doctor, "is_machine_home", lambda home=None: True)

    listening = _collect(home)["openclaw.gateway.listening"]

    assert opened and opened[0][:2] == ("127.0.0.1", 18789)
    assert listening.message != openclaw_doctor.doctor_runtime.check_listening_of_a_copy(CONFIG).message
    assert "18789" in listening.message


def test_an_injected_probe_still_decides_for_itself(tmp_path):
    """A caller that brings its own connector (a test) is not second-guessed."""
    home = _copy_home(tmp_path)
    asked: list[str] = []

    listening = _collect(home, connect=lambda host, port, timeout: asked.append(host) or True)[
        "openclaw.gateway.listening"
    ]

    assert asked == ["127.0.0.1"]
    assert listening.message == openclaw_doctor.doctor_runtime.check_listening(
        CONFIG, lambda host, port, timeout: True
    ).message


def test_the_machines_own_home_is_recognised_and_a_copy_is_not(tmp_path):
    assert paths.is_machine_home(Path.home()) is True
    assert paths.is_machine_home(tmp_path) is False
