"""The one stdio MCP mechanism both consumers use, against a real child process.

`nexgen_core.mcp_stdio` is what the HTTP bridge and `nexgen mcp check` both stand on,
and the official SDK does the spawning, framing and correlation under it. These tests
run a real Python child so they prove the mechanism, not a mock of it: the process
starts on the first call, the handshake is answered, a call is relayed, a child that
is gone is reported as such, and closing really stops it.

The child is a script in `tmp_path`: no test starts a connector of the owner's vault.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp.types import CONNECTION_CLOSED  # noqa: E402

from nexgen_core.mcp_stdio import StdioSession  # noqa: E402

#: A legacy MCP server: it echoes the handshake version, lists one tool, echoes a call,
#: and can be told to answer once and exit, which is how "the backend died" is produced.
FAKE_CHILD = '''
import json
import os
import sys

for line in sys.stdin:
    request = json.loads(line)
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": request["params"]["protocolVersion"],
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        arguments = request["params"].get("arguments") or {}
        if arguments.get("fail"):
            print(json.dumps({"jsonrpc": "2.0", "id": request_id,
                              "error": {"code": -32000, "message": "bye"}}), flush=True)
            continue
        result = {"content": [{"type": "text", "text": "said " + str(arguments.get("say", ""))}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id,
                          "error": {"code": -32601, "message": "not found"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
    if method == "tools/call" and (request["params"].get("arguments") or {}).get("exit"):
        sys.stdout.flush()
        os._exit(0)
'''


@pytest.fixture
def child(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD, encoding="utf-8")
    return [sys.executable, str(script)]


@pytest.fixture
def session(child):
    live = StdioSession(child, env={})
    try:
        yield live
    finally:
        live.close()


def test_the_first_call_starts_the_child_and_lists_its_tools(session):
    """Nothing is spawned until somebody asks: that is why the bridge can front five connectors."""
    assert session.call("tools/list", {}, 30.0) == {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}


def test_a_call_is_relayed_with_its_arguments(session):
    reply = session.call("tools/call", {"name": "echo", "arguments": {"say": "hello"}}, 30.0)
    assert reply["content"][0]["text"] == "said hello"


def test_initialize_answers_with_the_negotiated_handshake(session):
    """The caller's client has to be told the session that actually exists."""
    handshake = session.call("initialize", None, 30.0)
    assert handshake["serverInfo"] == {"name": "fake", "version": "1.0"}
    assert handshake["capabilities"] == {"tools": {}}
    assert isinstance(handshake["protocolVersion"], str) and handshake["protocolVersion"]


def test_a_child_error_is_the_sdk_exception_with_its_own_code(session):
    with pytest.raises(MCPError) as raised:
        session.call("tools/call", {"name": "echo", "arguments": {"fail": True}}, 30.0)
    assert (raised.value.code, raised.value.message) == (-32000, "bye")


def test_a_child_that_is_gone_is_a_closed_connection_and_a_restart_brings_a_new_one(session):
    session.call("tools/call", {"name": "echo", "arguments": {"exit": True}}, 30.0)
    for _ in range(100):
        try:
            session.call("tools/list", {}, 5.0)
        except MCPError as exc:
            assert exc.code == CONNECTION_CLOSED
            break
    else:
        pytest.fail("a child that exited must not answer again")

    session.restart()
    assert session.call("tools/list", {}, 30.0)["tools"][0]["name"] == "echo"


def test_a_closed_session_refuses_further_work(child):
    live = StdioSession(child, env={})
    live.close()
    with pytest.raises(RuntimeError, match="closed"):
        live.call("tools/list", {}, 5.0)


def test_nexgen_mcp_check_reads_a_real_child_through_the_same_mechanism(child):
    """`nexgen mcp check` keeps its own interface: a handshake, then dict results."""
    from nexgen_core import mcp_check

    check = mcp_check._Session(child, {})  # noqa: SLF001 - the interface the pinning test substitutes
    try:
        check.handshake(30.0)
        assert check.request("tools/list", None, 30.0)["tools"][0]["name"] == "echo"
        reply = check.request("tools/call", {"name": "echo", "arguments": {"say": "hi"}}, 30.0)
        assert reply["content"][0]["text"] == "said hi"
    finally:
        check.close()


def test_nexgen_mcp_check_turns_a_silent_child_into_a_deadline(tmp_path):
    """A command that hangs is worse than one that fails: the deadline is the promise."""
    from nexgen_core import mcp_check

    mute = tmp_path / "mute.py"
    mute.write_text("import sys, time\nsys.stdin.read(1)\ntime.sleep(60)\n", encoding="utf-8")
    check = mcp_check._Session([sys.executable, str(mute)], {})  # noqa: SLF001
    try:
        with pytest.raises(TimeoutError) as raised:
            check.handshake(1.0)
        assert "initialize" in str(raised.value)
    finally:
        check.close()
