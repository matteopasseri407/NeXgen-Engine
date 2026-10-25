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
    from nexgen_core.openclaw.node_cli import SERVICE_TIMEOUT_S

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
    # Installing the service outlasts a question, and still has a ceiling
    assert runner.call_args[0][1] == SERVICE_TIMEOUT_S
    # Target file path passed, NOT the literal secret token in command line
    assert secret_join_target not in cmd

    # Assert secret token is never logged in output
    for call in out.call_args_list:
        msg = str(call[0][0])
        assert secret_join_target not in msg


def test_check_node_connected() -> None:
    runner = MagicMock()
    # Windows Scheduled Task: running
    win_running = (
        '{"service": {"label": "Scheduled Task", "loaded": true, "command": '
        '{"programArguments": ["node.exe", "index.js", "node", "run", "--host", "gw.tailnet", "--port", "443", "--tls"]}, '
        '"runtime": {"status": "running"}}}'
    )
    runner.return_value = RunResult(0, win_running, "")
    assert check_node_connected(runner) is True

    # Windows Scheduled Task: stopped (real owner machine case)
    win_stopped = (
        '{"service": {"label": "Scheduled Task", "loaded": true, "command": '
        '{"programArguments": ["node.exe", "index.js", "node", "run", "--host", "gw.tailnet", "--port", "443", "--tls"]}, '
        '"runtime": {"status": "stopped", "state": "Ready"}}}'
    )
    runner.return_value = RunResult(0, win_stopped, "")
    assert check_node_connected(runner) is False

    # Linux systemd: running
    linux_running = (
        '{"service": {"label": "systemd", "loaded": true, "command": '
        '{"programArguments": ["/usr/bin/node", "index.js", "node", "run", "--host", "gw.tailnet", "--port", "18789"]}, '
        '"runtime": {"status": "running"}}}'
    )
    runner.return_value = RunResult(0, linux_running, "")
    assert check_node_connected(runner) is True

    # Linux systemd: stopped
    linux_stopped = (
        '{"service": {"label": "systemd", "loaded": true, "command": '
        '{"programArguments": ["/usr/bin/node", "index.js", "node", "run", "--host", "gw.tailnet", "--port", "18789"]}, '
        '"runtime": {"status": "stopped"}}}'
    )
    runner.return_value = RunResult(0, linux_stopped, "")
    assert check_node_connected(runner) is False

    # No service loaded
    no_service = '{"service": {"label": "Scheduled Task", "loaded": false, "command": null, "runtime": {"status": "stopped"}}}'
    runner.return_value = RunResult(0, no_service, "")
    assert check_node_connected(runner) is False

    # Command execution failure
    runner.return_value = RunResult(1, "", "daemon inspection failure")
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


def test_attach_uses_the_service_deadline_to_connect_and_the_read_deadline_to_check() -> None:
    """Two different jobs, two different ceilings: connecting installs, checking only asks."""
    from nexgen_core.openclaw.node_cli import READ_TIMEOUT_S, SERVICE_TIMEOUT_S

    attach_calls: list[tuple[list[str], float]] = []
    probe_calls: list[tuple[list[str], float]] = []

    def attach_runner(argv, timeout):
        attach_calls.append((list(argv), timeout))
        return RunResult(0, "connected\n", "")

    def probe_runner(argv, timeout):
        probe_calls.append((list(argv), timeout))
        return RunResult(0, (
            '{"service": {"label": "Scheduled Task", "loaded": true, "command": '
            '{"programArguments": ["node.exe", "index.js", "node", "run", "--host", "gw.example.com", "--port", "443", "--tls"]}, '
            '"runtime": {"status": "running"}}}'
        ), "")

    assert connect_node_service("https://gw.example.com/j/code", runner=attach_runner, out=lambda _: None) is True
    assert check_node_connected(probe_runner) is True

    assert attach_calls[0][1] == SERVICE_TIMEOUT_S
    assert probe_calls[0] == (["openclaw", "node", "status", "--json"], READ_TIMEOUT_S)
