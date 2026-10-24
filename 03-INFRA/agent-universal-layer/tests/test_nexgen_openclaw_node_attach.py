"""Tests for node attach connection progress monitoring and timeout recovery."""
from __future__ import annotations

from unittest.mock import MagicMock

from nexgen_core.openclaw.node_attach import (
    check_node_connected,
    connect_node_service,
    wait_for_node_connection,
)
from nexgen_core.openclaw_service import RunResult


def test_connect_node_service_uses_target_file_and_protects_secrets() -> None:
    runner = MagicMock()
    runner.return_value = RunResult(0, "connected\n", "")
    out = MagicMock()

    secret_join_target = "https://gw.example.com/j/super-secret-token-12345"
    ok = connect_node_service(secret_join_target, runner=runner, out=out)
    assert ok is True

    # Assert runner was called with --target-file
    assert runner.called
    cmd = runner.call_args[0][0]
    assert cmd[0:4] == ["openclaw", "connect", "--service", "--session-host"]
    assert cmd[4] == "--target-file"
    # Target file path passed, NOT the literal secret token in command line
    assert secret_join_target not in cmd

    # Assert secret token is never logged in output
    for call in out.call_args_list:
        msg = str(call[0][0])
        assert secret_join_target not in msg


def test_check_node_connected() -> None:
    runner = MagicMock()
    runner.return_value = RunResult(0, '{"connected": true, "status": "connected"}', "")
    assert check_node_connected(runner) is True

    runner.return_value = RunResult(0, '{"connected": false, "status": "disconnected"}', "")
    assert check_node_connected(runner) is False

    runner.return_value = RunResult(1, "", "gateway down")
    assert check_node_connected(runner) is False


def test_wait_for_node_connection_success() -> None:
    current_time = 0.0

    def fake_time() -> float:
        nonlocal current_time
        return current_time

    def fake_sleep(duration: float) -> None:
        nonlocal current_time
        current_time += duration

    calls = 0

    def probe() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    emitted: list[str] = []
    connected, elapsed = wait_for_node_connection(
        probe_fn=probe,
        node_name="win-desktop",
        timeout_s=30.0,
        report_interval_s=1.0,
        sleeper=fake_sleep,
        time_provider=fake_time,
        emit=emitted.append,
    )

    assert connected is True
    assert elapsed >= 0.0
    assert any("connected to gateway" in line for line in emitted)


def test_wait_for_node_connection_timeout_cites_only_documented_commands() -> None:
    current_time = 0.0

    def fake_time() -> float:
        nonlocal current_time
        return current_time

    def fake_sleep(duration: float) -> None:
        nonlocal current_time
        current_time += duration

    emitted: list[str] = []
    connected, elapsed = wait_for_node_connection(
        probe_fn=lambda: False,
        node_name="win-desktop",
        timeout_s=10.0,
        report_interval_s=2.0,
        sleeper=fake_sleep,
        time_provider=fake_time,
        emit=emitted.append,
    )

    assert connected is False
    assert elapsed >= 10.0
    assert any("did not connect within" in line for line in emitted)
    full_output = " ".join(emitted)
    assert "Recovery steps" in full_output
    assert "openclaw node status" in full_output
    assert "openclaw node restart" in full_output
    assert "openclaw devices join-code" in full_output
    # Must NOT cite non-existent openclaw node logs
    assert "node logs" not in full_output
