"""The smoke test a pin needs: five checks, one command, a clear verdict.

After a version change the only question that matters is whether a turn still comes
back. These tests drive the command with a recorder, so no test starts OpenClaw and
"the gateway answered" is shown rather than assumed.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.smoke import PROBE_MESSAGE, run_smoke  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw_service import RunResult  # noqa: E402 - sys.path shim for cloned checkout


class Recorder:
    """Answers like a healthy OpenClaw, and remembers every question."""

    def __init__(self, *, validate: int = 0, turn: str = "ok\n") -> None:
        self.validate = validate
        self.turn = turn
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float) -> RunResult:
        self.calls.append(list(argv))
        if argv[:2] == ["openclaw", "--version"]:
            return RunResult(0, "OpenClaw 2026.10.6\n", "")
        if argv[:2] == ["openclaw", "config"]:
            return RunResult(self.validate, "" if self.validate == 0 else "Config needs correction\n", "")
        if argv[:2] == ["openclaw", "mcp"]:
            return RunResult(0, "ok\n", "")
        if argv[:2] == ["openclaw", "agent"]:
            return RunResult(0, self.turn, "")
        return RunResult(127, "", "unexpected")


def test_a_healthy_gateway_passes_every_check(capsys):
    recorder = Recorder()

    assert run_smoke(run=recorder, health_fn=lambda: {"status": "ok"}) == 0

    printed = capsys.readouterr().out
    assert printed.count("PASS") == 5
    assert "The gateway is up and answering." in printed


def test_the_turn_really_reaches_the_agent(capsys):
    recorder = Recorder()

    run_smoke(run=recorder, health_fn=lambda: {"status": "ok"})

    assert ["openclaw", "agent", "main", "--message", PROBE_MESSAGE] in recorder.calls


def test_an_invalid_configuration_fails_the_run(capsys):
    assert run_smoke(run=Recorder(validate=1), health_fn=lambda: {}) == 1

    printed = capsys.readouterr().out
    assert "FAIL  the configuration is valid" in printed
    assert "1 check(s) failed" in printed


def test_a_gateway_that_does_not_answer_fails_the_run(capsys):
    def broken():
        raise RuntimeError("connection refused")

    assert run_smoke(run=Recorder(), health_fn=broken) == 1
    assert "connection refused" in capsys.readouterr().out


def test_an_empty_turn_is_not_a_working_agent(capsys):
    assert run_smoke(run=Recorder(turn="   \n"), health_fn=lambda: {}) == 1

    printed = capsys.readouterr().out
    assert "FAIL  agent main answers a short turn" in printed
