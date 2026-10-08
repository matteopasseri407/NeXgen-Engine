"""lazy-mcp serving: concurrent requests, visible failure causes, a filtered child environment, read/write split.

Found on a real machine: one slow call held a ping and a call to another server until it finished (a
600-second n8n tool timeout would have frozen every server behind it for ten minutes); the `drive`
server died on an import error and the proxy answered "tool not found on drive", with the cause thrown
away; every child inherited every token in the proxy's environment; and one tool carried both reads and
writes, so a CLI permission for it was all or nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from test_nexgen_lazy_mcp import LAZY_MCP, _stop_waiter, _write_manifest

# A server whose tools sleep for as long as the arguments say, and which can report its environment.
SLOW_SERVER = r"""
import json, os, sys, time
TOOLS = [
  {"name": "sleep", "description": "sleeps", "inputSchema": {"type": "object", "properties": {"s": {"type": "number"}}}},
  {"name": "env", "description": "reports names", "inputSchema": {"type": "object", "properties": {}}},
  {"name": "write", "description": "writes", "inputSchema": {"type": "object", "properties": {}}},
]
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    m, rid = req.get("method"), req.get("id")
    if m in ("initialize", "notifications/initialized", "ping"):
        continue
    if m == "tools/list":
        out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": TOOLS}}
    elif m == "tools/call":
        p = req["params"]
        if p["name"] == "sleep":
            time.sleep(float(p["arguments"].get("s", 0)))
            text = "slept"
        elif p["name"] == "env":
            text = json.dumps(sorted(os.environ))
        else:
            text = "wrote"
        out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": text}]}}
    else:
        out = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "nf"}}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
"""

DYING_SERVER = r"""
import sys
sys.stderr.write("Traceback (most recent call last):\n  File 'x.py', line 1\nModuleNotFoundError: No module named 'mcp'\n")
sys.stderr.flush()
sys.exit(1)
"""


def manifest(servers: dict[str, dict]) -> str:
    out = ["schema_version: 1", "retired_servers: []", "servers:"]
    for name, spec in servers.items():
        out += [f"  {name}:", "    lazy: true", "    command: python3",
                f"    args: [\"-c\", {json.dumps(spec['code'])}]"]
        if spec.get("readonly_tools"):
            out.append(f"    readonly_tools: {json.dumps(spec['readonly_tools'])}")
        if spec.get("readonly"):
            out.append("    readonly: true")
        if spec.get("env"):
            out.append("    env: " + json.dumps(spec["env"]))
        out.append("    timeouts: { startup: 10, tool: 30 }")
        out.append("    targets: [claude]")
    return "\n".join(out) + "\n"


class Waiter:
    """The proxy as a client sees it: requests written without waiting, replies read as they come."""

    def __init__(self, tmp_path: Path, servers: dict[str, dict], extra_env: dict[str, str] | None = None):
        vault = tmp_path / "vault"
        _write_manifest(vault, manifest(servers))
        env = {**os.environ, "AGENT_VAULT_DATA": str(vault), "LAZY_MCP_LOG": str(tmp_path / "audit.jsonl"),
               **(extra_env or {})}
        self.proc = subprocess.Popen(["python3", str(LAZY_MCP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, env=env,
                                     start_new_session=os.name == "posix")
        self.audit = tmp_path / "audit.jsonl"

    def send(self, rid: int, method: str, params: dict | None = None) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()

    def call(self, _rid: int, _meta_tool: str, **arguments) -> None:
        """`arguments` are the meta tool's own (server, tool, arguments, confirm): hence the underscores."""
        self.send(_rid, "tools/call", {"name": _meta_tool, "arguments": arguments})

    def read(self) -> dict:
        return json.loads(self.proc.stdout.readline())

    def ask(self, _rid: int, _meta_tool: str, **arguments) -> dict:
        self.call(_rid, _meta_tool, **arguments)
        return self.read()["result"]

    def text(self, result: dict) -> str:
        return result["content"][0]["text"]

    def close(self) -> None:
        _stop_waiter(self.proc)


@pytest.fixture
def waiter(tmp_path, request):
    made: list[Waiter] = []

    def make(servers, extra_env=None):
        w = Waiter(tmp_path, servers, extra_env)
        made.append(w)
        return w

    yield make
    for w in made:
        w.close()


# ------------------------------------------------------------------ concurrency


def test_a_slow_call_does_not_hold_a_ping_or_a_call_to_another_server(waiter):
    w = waiter({
        "slow": {"code": SLOW_SERVER, "readonly": True},
        "quick": {"code": SLOW_SERVER, "readonly": True},
    })
    w.ask(1, "lazy_list")  # warm both servers so only the calls are timed
    started = time.monotonic()
    w.call(2, "lazy_call", server="slow", tool="sleep", arguments={"s": 4})
    time.sleep(0.3)  # the slow call is in flight

    w.send(3, "ping")
    ping = w.read()
    ping_after = time.monotonic() - started
    w.call(4, "lazy_call", server="quick", tool="sleep", arguments={"s": 0})
    quick = w.read()
    quick_after = time.monotonic() - started
    slow = w.read()
    slow_after = time.monotonic() - started

    assert ping["id"] == 3 and quick["id"] == 4 and slow["id"] == 2
    assert ping_after < 2 and quick_after < 2, (ping_after, quick_after)  # they did not wait for the 4-second call
    assert slow_after >= 4


def test_one_servers_calls_stay_serial(waiter):
    """Its pipe is one stream: two calls must not interleave their frames."""
    w = waiter({"one": {"code": SLOW_SERVER, "readonly": True}})
    w.ask(1, "lazy_list")
    started = time.monotonic()
    w.call(2, "lazy_call", server="one", tool="sleep", arguments={"s": 1})
    w.call(3, "lazy_call", server="one", tool="sleep", arguments={"s": 1})
    replies = {w.read()["id"] for _ in range(2)}
    assert replies == {2, 3}
    assert time.monotonic() - started >= 2


def test_replies_are_whole_lines_even_under_concurrent_load(waiter):
    w = waiter({f"s{i}": {"code": SLOW_SERVER, "readonly": True} for i in range(4)})
    w.ask(1, "lazy_list")
    for i in range(40):
        w.call(100 + i, "lazy_call", server=f"s{i % 4}", tool="sleep", arguments={"s": 0})
    by_id = {}
    for _ in range(40):
        reply = w.read()  # json.loads raises if two replies were written into one line
        # Past MAX_CONCURRENT_REQUESTS the gateway answers a structured
        # -32000 error instead of running the call: success and refusal
        # are both whole, id-correlated lines.
        ok = reply.get("result", {}).get("content", [{}])[0].get("text") == "slept"
        refused = reply.get("error", {}).get("code") == -32000
        assert ok or refused, reply
        by_id[reply["id"]] = reply
    assert set(by_id) == {100 + i for i in range(40)}
    # The first call always finds a free slot, so it must succeed.
    assert by_id[100]["result"]["content"][0]["text"] == "slept"


def test_excess_concurrent_calls_get_structured_refusals(waiter):
    """Past the concurrency cap the gateway refuses with id-correlated
    -32000 errors instead of dropping replies or breaking framing."""
    w = waiter({"s": {"code": SLOW_SERVER, "readonly": True}},
               extra_env={"LAZY_MCP_MAX_CONCURRENCY": "1"})
    w.ask(1, "lazy_list")
    w.call(2, "lazy_call", server="s", tool="sleep", arguments={"s": 5})
    time.sleep(0.5)  # the only slot is held
    for rid in (3, 4, 5):
        w.call(rid, "lazy_call", server="s", tool="sleep", arguments={"s": 0})
    refused = set()
    for _ in range(3):
        reply = w.read()
        assert reply["id"] in (3, 4, 5)
        assert reply["error"]["code"] == -32000
        refused.add(reply["id"])
    assert refused == {3, 4, 5}


# ------------------------------------------------------------------ failure causes


def test_a_server_that_dies_says_why_instead_of_tool_not_found(waiter):
    w = waiter({"drive": {"code": DYING_SERVER, "readonly": True}})

    listed = json.loads(w.text(w.ask(1, "lazy_list")))
    loaded = w.ask(2, "lazy_load", server="drive", tool="drive_search")

    assert "ModuleNotFoundError: No module named 'mcp'" in listed["servers"]["drive"]["error"]
    assert loaded["isError"] is True
    assert "unavailable" in w.text(loaded) and "No module named 'mcp'" in w.text(loaded)
    assert "not found on drive" not in w.text(loaded)


def test_the_cause_never_carries_a_secret_the_proxy_handed_the_child(waiter):
    leaking = (
        "import os, sys\n"
        "sys.stderr.write('connecting with ' + os.environ['MY_SERVICE_TOKEN'] + ' failed\\n')\n"
        "sys.stderr.write('Authorization: Bearer abcdefghij0123456789\\n')\n"
        "sys.stderr.write('invalid token: expired\\n')\n"
        "sys.exit(1)\n"
    )
    w = waiter({"leaky": {"code": leaking, "readonly": True, "env": {"MY_SERVICE_TOKEN": "s3cr3t-value-12345"}}})

    error = json.loads(w.text(w.ask(1, "lazy_list")))["servers"]["leaky"]["error"]

    assert "s3cr3t-value-12345" not in error and "abcdefghij0123456789" not in error
    assert "[redacted]" in error
    assert "expired" in error  # the part that says what is wrong survives


# ------------------------------------------------------------------ the child environment


def test_a_child_server_does_not_inherit_the_proxys_secrets(waiter):
    w = waiter(
        {"probe": {"code": SLOW_SERVER, "readonly": True}},
        extra_env={"GITHUB_TOKEN": "ghp_x", "OPENAI_API_KEY": "sk-x", "AWS_SECRET_ACCESS_KEY": "x",
                   "MY_PASSWORD": "x", "NEXGEN_SOMETHING_TOKEN": "x", "HARMLESS_SETTING": "kept?"},
    )
    names = set(json.loads(w.text(w.ask(1, "lazy_call", server="probe", tool="env", arguments={}))))

    assert not names & {"GITHUB_TOKEN", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "MY_PASSWORD", "NEXGEN_SOMETHING_TOKEN"}
    # What a runtime needs to start at all; Windows has no HOME, its home is USERPROFILE.
    assert {"PATH", "USERPROFILE" if os.name == "nt" else "HOME"} <= names
    assert "HARMLESS_SETTING" not in names  # not on the list: a server that needs it declares it


def test_a_secret_the_manifest_entry_declares_is_passed_on_purpose(waiter):
    w = waiter(
        {"probe": {"code": SLOW_SERVER, "readonly": True, "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}}},
        extra_env={"GITHUB_TOKEN": "ghp_x", "OPENAI_API_KEY": "sk-x"},
    )
    names = set(json.loads(w.text(w.ask(1, "lazy_call", server="probe", tool="env", arguments={}))))
    assert "GITHUB_TOKEN" in names and "OPENAI_API_KEY" not in names


# ------------------------------------------------------------------ reads and writes are different tools


def test_lazy_call_forwards_only_what_the_manifest_declares_read_only(waiter):
    w = waiter({"mixed": {"code": SLOW_SERVER, "readonly_tools": ["sleep"]}})

    read = w.ask(1, "lazy_call", server="mixed", tool="sleep", arguments={"s": 0})
    write_via_call = w.ask(2, "lazy_call", server="mixed", tool="write", arguments={}, confirm=True)

    assert read.get("isError") is not True and w.text(read) == "slept"
    assert write_via_call["isError"] is True
    assert "lazy_mutate" in w.text(write_via_call)  # the refusal says where the call has to go
    assert "wrote" not in w.text(write_via_call)


def test_lazy_mutate_needs_the_acknowledgement_and_is_audited(waiter):
    w = waiter({"mixed": {"code": SLOW_SERVER, "readonly_tools": ["sleep"]}})

    without = w.ask(1, "lazy_mutate", server="mixed", tool="write", arguments={})
    done = w.ask(2, "lazy_mutate", server="mixed", tool="write", arguments={}, confirm=True)

    assert without["isError"] is True
    assert done.get("isError") is not True and w.text(done) == "wrote"
    log = [json.loads(line) for line in w.audit.read_text(encoding="utf-8").splitlines()]
    assert [e["action"] for e in log if e["tool"] == "write"] == ["refused", "call"]
    assert log[-1]["confirmed"] is True


def test_the_two_tools_are_annotated_so_a_cli_can_treat_them_differently(waiter):
    w = waiter({"mixed": {"code": SLOW_SERVER}})
    w.send(1, "tools/list")
    tools = {t["name"]: t["annotations"] for t in w.read()["result"]["tools"]}
    assert tools["lazy_call"]["readOnlyHint"] is True and tools["lazy_call"]["destructiveHint"] is False
    assert tools["lazy_mutate"]["readOnlyHint"] is False and tools["lazy_mutate"]["destructiveHint"] is True
