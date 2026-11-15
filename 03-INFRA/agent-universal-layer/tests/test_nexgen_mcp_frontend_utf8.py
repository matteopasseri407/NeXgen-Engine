"""A character the machine's code page cannot encode must not kill a tool call.

On Windows a stdio front end inherits the console's legacy code page (cp1252 on an Italian install,
also when the pipe is not a console): a result carrying an arrow, a CJK name or a checkmark died with
`UnicodeEncodeError` and the client read only "calling tools/call: internal error", and an argument
carrying one arrived at the server as mojibake.

Both front ends now speak UTF-8 on stdin, stdout and stderr. These tests force the legacy code page
with `PYTHONIOENCODING`, which is what a Windows pipe really hands them, and read the replies as
UTF-8. Before the fix the trim shim dies on `tools/list` (nothing is written at all) and the gateway
hands the server a garbled argument.

The fake server reads and writes its own frames as UTF-8 bytes, so what is under test is the front
end's encoding and not the backend's.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import yaml

INFRA = Path(__file__).resolve().parents[2]
MCP_DIR = INFRA / "agent-universal-layer" / "mcp"
TRIM = MCP_DIR / "mcp-trim.py"
LAZY = MCP_DIR / "lazy-mcp.py"

#: Not in cp1252, and the character a real result carried when the call died.
ARROW = "→"
RESULT_TEXT = f"arrow {ARROW}"

TOOLS = [
    {
        "name": "echo",
        "description": f"returns {ARROW}",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }
]

#: One fake server for both front ends: it echoes the argument it was given, and its tool's
#: description carries the same character.
SERVER = r"""
import json, sys
TOOLS = __TOOLS__
for raw in iter(sys.stdin.buffer.readline, b""):
    line = raw.decode("utf-8").strip()
    if not line:
        continue
    req = json.loads(line)
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        continue
    if method == "tools/list":
        result = {"resultType": "complete", "tools": TOOLS}
    elif method == "tools/call":
        arguments = req["params"].get("arguments") or {}
        result = {"resultType": "complete", "content": [{"type": "text", "text": str(arguments.get("text", ""))}]}
    elif method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "utf8", "version": "1"}}
    else:
        result = {}
    out = json.dumps({"jsonrpc": "2.0", "id": rid, "result": result})
    sys.stdout.buffer.write(out.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
""".replace("__TOOLS__", json.dumps(TOOLS))


class Front:
    """A front end started as a CLI starts it: pipes, and the legacy code page it inherits."""

    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, *argv],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env={**os.environ, **env},
        )
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(line) for line in self.proc.stdout], daemon=True).start()
        self.n = 0

    def ask(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        self.n += 1
        # `ensure_ascii=False`: a real client sends the JSON it means, and a
        # request escaped to `\u2192` would never reach the encoding under test.
        line = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}},
                          ensure_ascii=False)
        self.proc.stdin.write(line)
        self.proc.stdin.write("\n")
        self.proc.stdin.flush()
        return json.loads(self.lines.get(timeout=timeout))

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _manifest(tmp_path: Path, body: str) -> None:
    path = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _trim_environment(tmp_path: Path) -> dict[str, str]:
    _manifest(
        tmp_path,
        yaml.safe_dump({"servers": {"svc": {"transport": "stdio", "command": sys.executable,
                                           "args": ["-c", SERVER], "exposure": "eager"}}}),
    )
    return {
        "AGENT_VAULT_DATA": str(tmp_path / "vault"),
        "AGENT_STATE_DIR": str(tmp_path / "state"),
        "NEXGEN_HOME": str(tmp_path / "home"),
        "PYTHONIOENCODING": "cp1252",
    }


def _gateway_environment(tmp_path: Path) -> dict[str, str]:
    entry = {
        "lazy": True,
        "command": sys.executable,
        "args": ["-c", SERVER],
        "readonly": True,
        "timeouts": {"startup": 10, "tool": 30},
        "targets": ["claude"],
    }
    _manifest(tmp_path, yaml.safe_dump({"schema_version": 1, "retired_servers": [], "servers": {"svc": entry}}))
    return {
        "AGENT_VAULT_DATA": str(tmp_path / "vault"),
        "AGENT_STATE_DIR": str(tmp_path / "state"),
        "NEXGEN_HOME": str(tmp_path / "home"),
        "LAZY_MCP_LOG": str(tmp_path / "audit.jsonl"),
        "LAZY_MCP_CLI": "claude",
        "PYTHONIOENCODING": "cp1252",
    }


def test_the_trim_shim_answers_a_result_the_legacy_code_page_cannot_encode(tmp_path: Path):
    front = Front([str(TRIM), "svc"], _trim_environment(tmp_path))
    try:
        front.ask("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        listed = front.ask("tools/list")

        assert "error" not in listed, listed
        assert listed["result"]["tools"][0]["description"] == TOOLS[0]["description"]
        called = front.ask("tools/call", {"name": "echo", "arguments": {"text": RESULT_TEXT}})

        assert "error" not in called, called
        assert called["result"]["content"][0]["text"] == RESULT_TEXT
    finally:
        front.close()


def test_the_gateway_hands_the_server_an_argument_the_legacy_code_page_cannot_encode(tmp_path: Path):
    front = Front([str(LAZY)], _gateway_environment(tmp_path))
    try:
        index = json.loads(
            front.ask("tools/call", {"name": "lazy_list", "arguments": {}})["result"]["content"][0]["text"]
        )
        assert index["servers"]["svc"]["tools"][0]["hint"] == TOOLS[0]["description"]

        called = front.ask(
            "tools/call",
            {"name": "lazy_call", "arguments": {"server": "svc", "tool": "echo", "arguments": {"text": RESULT_TEXT}}},
        )

        assert "error" not in called, called
        assert called["result"]["content"][0]["text"] == RESULT_TEXT
    finally:
        front.close()
