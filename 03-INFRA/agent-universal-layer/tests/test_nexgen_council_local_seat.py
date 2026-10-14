"""Local Council seats: OpenCode as the harness over a loopback inference server, whatever the runtime."""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "agent-universal-layer" / "council", ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import routing  # noqa: E402
from nexgen_core.config import ConfigError, load_council_config  # noqa: E402
from routing import CHANNEL_TO_CLI, seat_capabilities, seat_channel  # noqa: E402
from seat_process import _build_seat_command, is_local_seat  # noqa: E402

MINICPM = {
    "vendor": "local",
    "cli": "opencode",
    "model": "local/minicpm-v4.7-35b-a3b",
    "base_url": "http://127.0.0.1:8080/v1",
    "routing_label": "MiniCPM-V 4.7",
    "zero_retention": False,
}


def _load(tmp_path: Path, seat: dict) -> dict:
    path = tmp_path / "seats.yaml"
    path.write_text(json.dumps({"schema_version": 1, "seats": {"local-minicpm": seat}}), encoding="utf-8")
    return load_council_config(path)


def test_a_loopback_local_seat_is_valid(tmp_path):
    for url in ("http://127.0.0.1:8080/v1", "http://localhost:11434/v1", "http://[::1]:8080/v1"):
        assert _load(tmp_path, {**MINICPM, "base_url": url})["seats"]["local-minicpm"]["base_url"] == url


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"base_url": "http://203.0.113.5:8080/v1"}, "loopback"),
        ({"base_url": "ftp://127.0.0.1/v1"}, "loopback"),
        ({"base_url": ""}, "non-empty"),
        ({"cli": "ollama"}, "only for cli opencode"),
        ({"model": "minicpm-v4.7-35b-a3b"}, "<provider>/<model>"),
        ({"host": ""}, "hostname"),
    ],
)
def test_a_local_seat_that_could_leak_or_cannot_run_is_refused(tmp_path, override, message):
    with pytest.raises(ConfigError, match=message):
        _load(tmp_path, {**MINICPM, **override})


def test_local_inference_always_goes_through_opencode():
    assert CHANNEL_TO_CLI["local"] == "opencode"
    assert seat_channel(MINICPM) == "local"
    assert is_local_seat(MINICPM)
    hosted = {"vendor": "opencode", "cli": "opencode", "model": "opencode-go/kimi-k3"}
    assert seat_channel(hosted) == "go"
    assert not is_local_seat(hosted)


def test_the_seat_config_carries_only_its_own_provider(tmp_path):
    invocation = _build_seat_command(MINICPM, "brief", tmp_path)
    assert invocation.argv[invocation.argv.index("-m") + 1] == "local/minicpm-v4.7-35b-a3b"
    assert "--standalone" in invocation.argv
    written = Path(invocation.env["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
    config = json.loads(written.read_text(encoding="utf-8"))
    assert set(config) == {"provider"}
    assert set(config["provider"]) == {"local"}
    provider = config["provider"]["local"]
    assert provider["options"] == {"baseURL": "http://127.0.0.1:8080/v1"}
    assert set(provider["models"]) == {"minicpm-v4.7-35b-a3b"}


def test_a_hosted_opencode_seat_still_gets_an_empty_config(tmp_path):
    seat = {"vendor": "opencode", "cli": "opencode", "model": "opencode-go/kimi-k3"}
    invocation = _build_seat_command(seat, "brief", tmp_path)
    assert not (Path(invocation.env["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json").exists()


@pytest.fixture
def opencode_on_path(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda cli: f"/usr/bin/{cli}")


def test_a_seat_bound_to_another_host_is_unavailable_here(opencode_on_path):
    caps = seat_capabilities({"local-minicpm": {**MINICPM, "host": "some-other-machine"}})
    assert not caps["local-minicpm"].available
    assert "some-other-machine" in caps["local-minicpm"].reason


def test_a_local_seat_is_available_only_while_its_port_listens(opencode_on_path):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    seat = {**MINICPM, "base_url": f"http://127.0.0.1:{port}/v1", "host": socket.gethostname()}
    try:
        assert seat_capabilities({"s": seat})["s"].available
    finally:
        server.close()
    assert not seat_capabilities({"s": seat})["s"].available


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the listen table is Linux-only")
def test_the_probe_never_connects_on_linux(monkeypatch, opencode_on_path):
    """A connection to a socket-activated port would start the model (and stop the voice cockpit)."""

    def refuse(*args, **kwargs):
        raise AssertionError("the probe opened a connection")

    monkeypatch.setattr(routing.socket, "create_connection", refuse)
    seat_capabilities({"s": MINICPM})
