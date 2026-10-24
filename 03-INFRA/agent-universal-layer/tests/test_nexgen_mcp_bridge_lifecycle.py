"""The bridge's life: what `start` leaves behind, what `status` believes, and what `stop` removes.

No test here launches a real process: `start` is given a spawner that plays the
detached listener (a real HTTP server, in this process), so the rules that matter
are exercised end to end without touching the machine. In particular the state
file is the thing under test, because a state file that lies is how a stop command
kills a stranger or a render writes a URL nothing answers on.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_bridge import (  # noqa: E402
    BridgeError,
    main as bridge_main,
    bridged_http_servers,
    run_bridge,
    start_bridge,
    status_bridge,
    stop_bridge,
)
from nexgen_core.mcp_bridge_server import McpHttpBridgeServer  # noqa: E402
from nexgen_core.mcp_bridge_state import state_file_path, write_state  # noqa: E402

TOKEN = "lifecycle-token-for-the-stand-in"
FAKE_MCP = "import sys\nfor line in sys.stdin:\n    pass\n"


@pytest.fixture(autouse=True)
def never_taskkill(monkeypatch):
    """No `taskkill` on the machine that runs the suite."""
    def _kill(proc, *, process_group=None, timeout=5.0):
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=timeout)
        except Exception:  # noqa: BLE001 - a test double that will not die must not fail the test
            pass
        return True

    monkeypatch.setattr("nexgen_core.mcp_bridge_channel.force_stop_process_tree", _kill)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch):
    """A fake home: the state file and the token deposit both stay inside `tmp_path`."""
    monkeypatch.setenv("NEXGEN_SECRETS_ENV", str(tmp_path / "secrets.env"))
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("NEXGEN_MCP_BRIDGE_TOKEN", raising=False)
    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_MCP, encoding="utf-8")
    return {
        "home": tmp_path,
        "backends": {"fake-tool": {"command": [sys.executable, str(script)], "env": {}}},
    }


class FakeRenderer:
    """The renderer's answer, without reading a manifest: the bridge only asks this."""

    def __init__(self, servers: dict) -> None:
        self.servers = servers

    def load_resolved_servers(self, cli_target: str) -> dict:
        assert cli_target == "openclaw"
        return self.servers


def _post(url: str, token: str = TOKEN, payload: dict | None = None):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload or {}).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:
        return response.status, json.loads(response.read().decode("utf-8") or "{}")


def _get(url: str, token: str = TOKEN) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=5.0) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_run_bridge_records_itself_serves_and_cleans_up(sandbox):
    """The state file is written before the first request and removed when the listener leaves."""
    state_path = state_file_path(sandbox["home"])
    thread = threading.Thread(
        target=run_bridge,
        args=("127.0.0.1", 0, TOKEN, sandbox["backends"], "127.0.0.1", state_path),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    state: dict = {}
    while time.monotonic() < deadline:
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            break
        time.sleep(0.05)
    assert state, "run_bridge must record the run so a later command can find it"
    assert state["servers"] == ["fake-tool"]
    assert "nonce" in state
    assert TOKEN not in state_path.read_text(encoding="utf-8"), "the token is never written here"

    health = _get(f"http://127.0.0.1:{state['port']}/health")
    assert health["nonce"] == state["nonce"]

    _post(f"http://127.0.0.1:{state['port']}/shutdown")
    thread.join(10)
    assert not thread.is_alive()
    assert not state_path.exists(), "a stopped bridge leaves no state behind"
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://127.0.0.1:{state['port']}/health", timeout=2.0)


def test_start_status_and_stop_over_a_stand_in_listener(sandbox):
    """`start` waits for a listener that answers, and reports the connectors a gateway can address."""
    home = sandbox["home"]
    state_path = state_file_path(home)
    started: list[McpHttpBridgeServer] = []

    def spawn(argv: list[str]) -> int:
        assert any(argument == "--server=fake-tool" for argument in argv), argv
        token = os.environ["NEXGEN_MCP_BRIDGE_TOKEN"]
        server = McpHttpBridgeServer(
            ("127.0.0.1", 0), token=token, backends=sandbox["backends"], nonce="stand-in-nonce"
        )
        write_state(state_path, {
            "pid": 4242,
            "nonce": "stand-in-nonce",
            "bind_host": "127.0.0.1",
            "client_host": "127.0.0.1",
            "port": server.server_address[1],
            "url": server.get_url(),
            "token_env": "NEXGEN_MCP_BRIDGE_TOKEN",
            "servers": server.served_names(),
            "started_at": server.started_at,
        })
        server.start_in_thread()
        started.append(server)
        return 4242

    try:
        info = start_bridge(home=home, spawn=spawn, renderer=FakeRenderer(sandbox["backends"]))
        assert info["status"] == "started"
        assert info["pid"] == 4242
        assert info["servers"] == ["fake-tool"]

        status = status_bridge(home)
        assert status["running"] is True
        assert status["verified"] is True

        bridged = bridged_http_servers(home)
        assert bridged == {
            "fake-tool": {
                "url": f"http://127.0.0.1:{info['port']}/mcp/fake-tool",
                "token_env": "NEXGEN_MCP_BRIDGE_TOKEN",
            }
        }

        assert start_bridge(home=home, spawn=spawn, renderer=FakeRenderer(sandbox["backends"]))["status"] == "already_running"
        assert len(started) == 1, "a second start must not spawn a second listener"

        stopped = stop_bridge(home)
        assert stopped["stopped"] is True
        assert not status_bridge(home)["running"]
        assert not state_path.exists()
        assert bridged_http_servers(home) == {}
    finally:
        for server in started:
            server.shutdown_and_close()


def test_a_state_file_from_a_run_that_ended_is_recognised_and_never_believed(sandbox):
    """A leftover file must not make `status` claim a listener, nor a renderer write its URL."""
    home = sandbox["home"]
    write_state(state_file_path(home), {
        "pid": 999_999,
        "nonce": "gone",
        "bind_host": "127.0.0.1",
        "client_host": "127.0.0.1",
        "port": _free_port(),
        "url": "http://127.0.0.1:1/mcp",
        "token_env": "NEXGEN_MCP_BRIDGE_TOKEN",
        "servers": ["fake-tool"],
    })
    status = status_bridge(home)
    assert status["running"] is False
    assert status["stale"] is True
    assert bridged_http_servers(home) == {}

    result = stop_bridge(home)
    assert result["stopped"] is True
    assert not state_file_path(home).exists()


def test_a_name_that_is_not_a_connector_is_refused_with_the_list(sandbox):
    with pytest.raises(BridgeError) as raised:
        start_bridge(home=sandbox["home"], names=["nope"], renderer=FakeRenderer(sandbox["backends"]))
    assert "fake-tool" in str(raised.value), "the phrase must say what could have been asked for"


def test_nothing_to_bridge_is_a_sentence(sandbox):
    with pytest.raises(BridgeError) as raised:
        start_bridge(home=sandbox["home"], renderer=FakeRenderer({}))
    assert "Nothing to bridge" in str(raised.value)


def test_the_child_entry_point_answers_status_and_stop(sandbox, capsys):
    """`start` spawns exactly this entry point, so it has to work when nothing is running."""
    assert bridge_main(["status"]) == 1
    assert '"running": false' in capsys.readouterr().out

    assert bridge_main(["stop"]) == 0
    assert "not running" in capsys.readouterr().out.lower()
