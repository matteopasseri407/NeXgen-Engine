"""`nexgen openclaw restart` and `nexgen openclaw pair`, with fakes only.

The two verbs a person reaches for after setup: bring the gateway back and let
a phone in. Neither test runs `openclaw`, opens a port, or prints a token.
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
from nexgen_core.openclaw.setup import run_pair, run_restart, run_stop  # noqa: E402
from nexgen_core.openclaw_service import RunResult  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


class FakeService:
    """Just enough of the service controller for `restart` and `stop`."""

    def __init__(self, port: int = 18789, result: dict | None = None, stop_result: dict | None = None):
        self.port = port
        self.result = result or {"ok": True, "commands": [["openclaw", "gateway", "install"]], "elapsed_s": 12.4}
        self.stop_result = stop_result or {
            "ok": True,
            "commands": [["systemctl", "--user", "stop", "openclaw-gateway.service"]],
            "remaining_pids": [],
        }

    def restart(self, wait: bool = True, timeout_s: float = 600.0, dry_run: bool = False):
        return dict(self.result)

    def stop(self, dry_run: bool = False):
        return dict(self.stop_result)


class PairRunner:
    """The executor `pairing.pending` and `pairing.approve` call."""

    def __init__(self, devices: list[dict] | None = None, approve_code: int = 0):
        self.devices = devices or []
        self.approve_code = approve_code
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[1:3] == ["devices", "list"]:
            return RunResult(0, json.dumps({"devices": self.devices}), "")
        if argv[1:3] == ["devices", "approve"]:
            return RunResult(self.approve_code, "", "" if self.approve_code == 0 else "unknown id")
        return RunResult(0, "", "")


def test_stop_reports_the_gateway_stopped():
    lines: list[str] = []

    code = run_stop(service=FakeService(), out=lines.append)

    assert code == 0
    assert any("systemctl" in line for line in lines)
    assert any("stopped" in line for line in lines)


def test_stop_that_leaves_a_process_alive_is_a_failure():
    service = FakeService(stop_result={"ok": False, "commands": [], "remaining_pids": [4321]})
    lines: list[str] = []

    code = run_stop(service=service, out=lines.append)

    assert code == 1
    assert any("4321" in line for line in lines)


def test_restart_prints_how_long_the_port_took():
    lines: list[str] = []

    code = run_restart(service=FakeService(), out=lines.append)

    assert code == 0
    assert any("12 seconds" in line for line in lines)


def test_restart_that_never_reaches_the_port_is_a_failure():
    lines: list[str] = []
    service = FakeService(result={"ok": False, "commands": [], "elapsed_s": 600.0, "error": "Timed out waiting for port 18789"})

    code = run_restart(service=service, out=lines.append)

    assert code == 1
    assert any("Timed out waiting for port 18789" in line for line in lines)


def test_pair_lists_a_waiting_device_without_its_token():
    lines: list[str] = []
    runner = PairRunner(devices=[
        {"id": "abc123", "summary": "Pixel 8", "status": "pending", "token": "SECRET-TOKEN-VALUE"},
    ])

    code = run_pair(runner=runner, out=lines.append)

    assert code == 0
    output = "\n".join(lines)
    assert "abc123" in output
    assert "Pixel 8" in output
    assert "SECRET-TOKEN-VALUE" not in output
    assert not any("approve" in call for call in runner.calls)


def test_pair_with_nothing_waiting_says_so():
    lines: list[str] = []

    assert run_pair(runner=PairRunner(), out=lines.append) == 0
    assert any("No device is waiting" in line for line in lines)


def test_pair_approves_only_after_the_person_says_yes():
    runner = PairRunner(devices=[{"id": "abc123", "summary": "Pixel 8"}])
    lines: list[str] = []
    asked: list[str] = []

    code = run_pair(
        approve_id="abc123",
        runner=runner,
        confirm=lambda question: asked.append(question) or True,
        out=lines.append,
    )

    assert code == 0
    assert asked and "abc123" in asked[0]
    assert ["openclaw", "devices", "approve", "abc123"] in runner.calls
    assert any("approved" in line for line in lines)


def test_pair_approves_nothing_when_the_person_declines():
    runner = PairRunner(devices=[{"id": "abc123", "summary": "Pixel 8"}])
    lines: list[str] = []

    code = run_pair(approve_id="abc123", runner=runner, confirm=lambda question: False, out=lines.append)

    assert code == 0
    assert not any("approve" in call for call in runner.calls)
    assert any("Nothing approved" in line for line in lines)


def test_pair_with_yes_does_not_ask():
    runner = PairRunner(devices=[{"id": "abc123", "summary": "Pixel 8"}])
    asked: list[str] = []

    code = run_pair(
        approve_id="abc123", yes=True, runner=runner,
        confirm=lambda question: asked.append(question) or False,
        out=lambda line: None,
    )

    assert code == 0
    assert asked == []
    assert ["openclaw", "devices", "approve", "abc123"] in runner.calls


def test_pair_reports_a_failed_approval_with_a_non_zero_code():
    runner = PairRunner(devices=[{"id": "abc123"}], approve_code=1)
    lines: list[str] = []

    code = run_pair(approve_id="abc123", yes=True, runner=runner, out=lines.append)

    assert code == 1
    assert any("failed" in line for line in lines)
