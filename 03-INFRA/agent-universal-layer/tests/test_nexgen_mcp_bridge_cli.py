"""`nexgen mcp bridge start|stop|status`: what the person reads, and what the command refuses to say.

The bridge's own behaviour is tested against a real listener elsewhere; these tests
are about the command surface. Two rules are worth pinning here: the token is never
printed, and a failure is a sentence on stderr with a non-zero exit, never a
traceback.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import build_parser  # noqa: E402
from nexgen_core.cli.tool_cmds import (  # noqa: E402
    cmd_mcp_bridge_start,
    cmd_mcp_bridge_status,
    cmd_mcp_bridge_stop,
)
from nexgen_core.mcp_bridge import BridgeError  # noqa: E402

TOKEN = "a-token-that-must-never-be-printed"


@pytest.fixture(autouse=True)
def token_in_the_environment(monkeypatch):
    monkeypatch.setenv("NEXGEN_MCP_BRIDGE_TOKEN", TOKEN)


def _args(**overrides):
    from argparse import Namespace

    values = {"host": None, "port": 8765, "wsl": False, "servers": None, "foreground": False}
    values.update(overrides)
    return Namespace(**values)


def test_the_three_actions_are_registered():
    parser = build_parser()
    assert parser.parse_args(["mcp", "bridge", "status"]).func is cmd_mcp_bridge_status
    assert parser.parse_args(["mcp", "bridge", "stop"]).func is cmd_mcp_bridge_stop
    parsed = parser.parse_args(["mcp", "bridge", "start", "--wsl", "--server", "one", "--server", "two"])
    assert parsed.func is cmd_mcp_bridge_start
    assert parsed.wsl is True
    assert parsed.servers == ["one", "two"]


def test_start_reports_the_url_and_the_connectors_without_the_token(monkeypatch, capsys):
    monkeypatch.setattr("nexgen_core.mcp_bridge.start_bridge", lambda **kwargs: {
        "status": "started",
        "url": "http://127.0.0.1:8765/mcp",
        "pid": 4321,
        "servers": ["lazy-mcp", "playwright"],
    })
    assert cmd_mcp_bridge_start(_args()) == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8765/mcp" in out
    assert "4321" in out
    assert "lazy-mcp, playwright" in out
    assert TOKEN not in out, "the token is a lock, not a message"


def test_start_says_so_when_it_was_already_running(monkeypatch, capsys):
    monkeypatch.setattr("nexgen_core.mcp_bridge.start_bridge", lambda **kwargs: {
        "status": "already_running",
        "url": "http://127.0.0.1:8765/mcp",
        "servers": ["lazy-mcp"],
    })
    assert cmd_mcp_bridge_start(_args()) == 0
    assert "already running" in capsys.readouterr().out.lower()


def test_start_turns_a_refusal_into_a_sentence_on_stderr(monkeypatch, capsys):
    def explode(**kwargs):
        raise BridgeError("No WSL network interface found: pass --host with the address the distro can reach")

    monkeypatch.setattr("nexgen_core.mcp_bridge.start_bridge", explode)
    assert cmd_mcp_bridge_start(_args(wsl=True)) == 2
    captured = capsys.readouterr()
    assert "No WSL network interface" in captured.err
    assert "Traceback" not in captured.err


def test_status_exit_code_tells_a_script_whether_the_bridge_is_up(monkeypatch, capsys):
    monkeypatch.setattr("nexgen_core.mcp_bridge.status_bridge", lambda: {
        "running": True, "url": "http://127.0.0.1:8765/mcp", "pid": 99, "servers": ["lazy-mcp"],
    })
    assert cmd_mcp_bridge_status(_args()) == 0
    assert "lazy-mcp" in capsys.readouterr().out

    monkeypatch.setattr("nexgen_core.mcp_bridge.status_bridge", lambda: {"running": False})
    assert cmd_mcp_bridge_status(_args()) == 1
    assert "not running" in capsys.readouterr().out.lower()

    monkeypatch.setattr("nexgen_core.mcp_bridge.status_bridge", lambda: {"running": False, "stale": True})
    assert cmd_mcp_bridge_status(_args()) == 1
    assert "state file" in capsys.readouterr().out


def test_stop_reports_the_three_outcomes(monkeypatch, capsys):
    monkeypatch.setattr("nexgen_core.mcp_bridge.stop_bridge", lambda: {"stopped": True})
    assert cmd_mcp_bridge_stop(_args()) == 0
    assert "stopped" in capsys.readouterr().out

    monkeypatch.setattr("nexgen_core.mcp_bridge.stop_bridge", lambda: {"stopped": False, "reason": "not running"})
    assert cmd_mcp_bridge_stop(_args()) == 0
    assert "not running" in capsys.readouterr().out.lower()

    monkeypatch.setattr("nexgen_core.mcp_bridge.stop_bridge", lambda: {
        "stopped": False, "reason": "still answering", "url": "http://127.0.0.1:8765/mcp", "pid": 1234,
    })
    assert cmd_mcp_bridge_stop(_args()) == 1
    assert "1234" in capsys.readouterr().out
