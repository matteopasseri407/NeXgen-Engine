"""This machine's side of becoming a node: the protected handoff and its approvals.

What is proved here is the handoff OpenClaw documents: the join target reaches
`openclaw connect` through a private file rather than its arguments, so a
single-use code never appears in the process list or in what the engine prints,
and a refused command is reported instead of being taken for a connection.

There is no wait loop to test: `openclaw connect --service` completes the first
authenticated connection before it installs the service, so its exit code is the
answer this engine used to poll for.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from nexgen_core.openclaw.node_attach import connect_node_service
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


def test_a_refused_connect_is_reported_and_is_not_a_connection() -> None:
    """The exit code is the only proof of attachment there is, so a failure never reads as one."""
    runner = MagicMock(return_value=RunResult(7, "", "join code not found or expired"))
    said: list[str] = []

    assert connect_node_service("https://gw.example.com/j/spent", runner=runner, out=said.append) is False

    output = "\n".join(said)
    assert "Node connect failed (exit code 7)" in output
    assert "completed" not in output


def test_the_handoff_file_is_gone_after_a_refused_connect(monkeypatch) -> None:
    """A spent or refused code must not be left on disk for the next run to redeploy."""
    from nexgen_core.openclaw import node_attach

    written: list[object] = []
    real_write = node_attach.atomic_write_text

    def remember(path, text, **kwargs):
        written.append(path)
        return real_write(path, text, **kwargs)

    monkeypatch.setattr(node_attach, "atomic_write_text", remember)

    connect_node_service(
        "https://gw.example.com/j/spent",
        runner=MagicMock(return_value=RunResult(1, "", "nope")),
        out=lambda _line: None,
    )

    assert written and written[0].exists() is False


def test_connecting_installs_the_service_under_the_long_deadline() -> None:
    """Connecting pairs and installs, so it gets the install ceiling, not the quick read one."""
    from nexgen_core.openclaw.node_cli import SERVICE_TIMEOUT_S

    calls: list[tuple[list[str], float]] = []

    def runner(argv, timeout):
        calls.append((list(argv), timeout))
        return RunResult(0, "connected\n", "")

    assert connect_node_service("https://gw.example.com/j/code", runner=runner, out=lambda _: None) is True

    assert calls[0][1] == SERVICE_TIMEOUT_S
