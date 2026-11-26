"""The groom against a real OpenClaw Gateway: every chat it would ask can be asked.

nexgen: one-job reason="judge the groom's session listing and compact call against a running pinned Gateway"

The other groom tests fake the CLI, so they prove what the engine believes. In
2026.11.10 no command, flag or field changed and the behaviour did: archived
sessions joined the listing and `compact` refused them. Here a throwaway Gateway
holds one live and one archived session, and OpenClaw itself answers. The CI job
`openclaw-contract` runs this on the pinned release and, nightly, on the latest
one. No model is called: an empty chat is answered "nothing to compact".
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import groom_memory, sessions_groom  # noqa: E402
from nexgen_core.openclaw.groom_lines import Decision  # noqa: E402
from nexgen_core.openclaw.node_cli import launch_argv  # noqa: E402
from nexgen_core.openclaw.sessions_archived import drop_archived  # noqa: E402

PIN = Path(__file__).resolve().parents[1] / "openclaw" / "pin.json"
PINNED = json.loads(PIN.read_text(encoding="utf-8"))["version"]
REQUIRED = os.environ.get("NEXGEN_REQUIRE_OPENCLAW") == "1"
LIVE = "agent:main:contract-live"
ARCHIVED = "agent:main:contract-archived"


def _binary() -> str | None:
    """The `openclaw` this run may judge: any in CI, only the pinned release elsewhere."""
    found = shutil.which("openclaw")
    if not found or REQUIRED:
        return found
    argv = [found, "--version"]
    said = subprocess.run(launch_argv(argv) or argv, capture_output=True, text=True, timeout=60,
                          check=False).stdout
    return found if PINNED in said else None


OPENCLAW = _binary()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _call(method: str, params: dict) -> dict:
    proc = sessions_groom._run([OPENCLAW, "gateway", "call", method, "--params", json.dumps(params), "--json",
                                "--timeout", "60000"], 120)
    assert proc.returncode == 0, f"{method}: {proc.stdout}{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture
def gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A Gateway of its own: its folder, its port, its token. Nothing of this machine's is read."""
    if OPENCLAW is None:
        pytest.fail("NEXGEN_REQUIRE_OPENCLAW=1 but no openclaw binary is on PATH")
    port = _free_port()
    state = tmp_path / ".openclaw"
    state.mkdir()
    config = state / "openclaw.json"
    config.write_text(json.dumps({"gateway": {"mode": "local", "port": port, "bind": "loopback",
                                              "auth": {"mode": "token", "token": "contract-placeholder"}}}),
                      encoding="utf-8")
    for name in ("OPENCLAW_GATEWAY_URL", "OPENCLAW_GATEWAY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (("HOME", tmp_path), ("USERPROFILE", tmp_path), ("OPENCLAW_STATE_DIR", state),
                        ("OPENCLAW_CONFIG_PATH", config), ("OPENCLAW_GATEWAY_PORT", port)):
        monkeypatch.setenv(name, str(value))
    log = tmp_path / "gateway.log"
    argv = [OPENCLAW, "gateway", "run", "--port", str(port)]
    with log.open("wb") as sink:
        server = subprocess.Popen(launch_argv(argv) or argv, stdout=sink, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 240
        while sessions_groom._run([OPENCLAW, "gateway", "call", "health", "--json"], 120).returncode != 0:
            assert server.poll() is None and time.monotonic() < deadline, log.read_text("utf-8", "replace")[-3000:]
            time.sleep(2)
        yield
    finally:
        server.kill()
        server.wait(timeout=30)


@pytest.mark.skipif(OPENCLAW is None and not REQUIRED, reason=f"no openclaw {PINNED} on PATH")
def test_every_chat_the_groom_lists_can_be_asked_to_compact(gateway) -> None:
    _call("sessions.create", {"key": LIVE})
    created = _call("sessions.create", {"key": ARCHIVED})
    _call("sessions.patch", {"key": ARCHIVED, "archived": True,
                             "expectedSessionId": created["entry"]["sessionId"]})

    rows = drop_archived(sessions_groom.list_sessions(OPENCLAW), OPENCLAW, sessions_groom._run)
    keys = {row["key"] for row in rows}

    assert LIVE in keys and ARCHIVED not in keys, keys
    for row in rows:
        assert row["agentId"] and "totalTokens" in row and row["updatedAt"], row
        decision = Decision(key=row["key"], agent_id=row["agentId"], tokens=0, idle_seconds=0.0,
                            reason="idle", runtime="", fingerprint={})
        outcome = sessions_groom.compact(OPENCLAW, decision)
        assert outcome.state != groom_memory.FAILED, f"{row['key']}: {outcome.line}"
