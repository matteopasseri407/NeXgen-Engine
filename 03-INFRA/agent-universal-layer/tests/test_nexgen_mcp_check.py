"""`nexgen mcp check` against a real gateway process and real (tiny) backends.

What it must prove: the gateway a CLI is configured to start really starts, serves exactly what the plan routes
behind it for that CLI, says why a backend is down, and leaves nothing running when it is done.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))
LAZY_MCP = INFRA / "agent-universal-layer" / "mcp" / "lazy-mcp.py"

from nexgen_core import mcp_check  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process semantics (pid files, signals)")

GOOD = r"""
import json, os, sys
open(os.environ["PIDFILE"], "w").write(str(os.getpid()))
TOOLS = [{"name": "ping_tool", "description": "pong", "inputSchema": {"type": "object", "properties": {}}}]
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        continue
    if method == "tools/list":
        out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": TOOLS}}
    else:
        out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete"}}
    sys.stdout.write(json.dumps(out) + "\n"); sys.stdout.flush()
"""

DYING = "import sys\nsys.stderr.write('ModuleNotFoundError: No module named missing_dep\\n')\nsys.exit(1)\n"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def setup(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    pidfile = tmp_path / "good.pid"
    servers = {
        "lazy-mcp": {"tier": "core", "command": sys.executable, "args": [str(LAZY_MCP)],
                     "env": {"AGENT_VAULT_DATA": str(vault), "LAZY_MCP_LOG": str(tmp_path / "audit.jsonl")}},
        "good": {"exposure": "lazy", "command": sys.executable, "args": ["-c", GOOD], "env": {"PIDFILE": str(pidfile)},
                 "targets": ["claude", "codex"], "readonly": True},
        "broken": {"exposure": "lazy", "command": sys.executable, "args": ["-c", DYING], "targets": ["claude"]},
        "elsewhere": {"exposure": "lazy", "command": sys.executable, "args": ["-c", GOOD], "env": {"PIDFILE": str(tmp_path / "e.pid")},
                      "targets": ["opencode"]},
    }
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    renderer = McpRenderer(vault_data=vault, engine_root=INFRA, home=tmp_path / "home")
    return renderer, pidfile


def test_a_cli_gets_exactly_the_servers_the_plan_routes_to_it(setup):
    renderer, _ = setup
    codex = mcp_check.check_gateway(renderer, "codex", timeout=60)
    assert codex.ok, codex.problems
    assert codex.served == ["good"]
    opencode = mcp_check.check_gateway(renderer, "opencode", timeout=60)
    assert opencode.ok, opencode.problems
    assert opencode.served == ["elsewhere"]


def test_a_backend_that_cannot_start_is_reported_with_its_reason(setup):
    renderer, _ = setup
    claude = mcp_check.check_gateway(renderer, "claude", timeout=60)
    assert claude.served == ["broken", "good"]
    assert claude.ok is False
    assert any(p.startswith("broken:") and "missing_dep" in p for p in claude.problems), claude.problems


def test_nothing_is_left_running_afterwards(setup):
    renderer, pidfile = setup
    mcp_check.check_gateway(renderer, "codex", timeout=60)
    pid = int(pidfile.read_text())
    for _ in range(50):
        if not alive(pid):
            break
        time.sleep(0.1)
    assert not alive(pid), "the backend outlived the gateway that started it"


def test_a_gateway_not_mounted_where_servers_are_routed_is_a_failure(setup, tmp_path):
    renderer, _ = setup
    manifest = yaml.safe_load(renderer.manifest_path.read_text())
    manifest["servers"]["lazy-mcp"]["targets"] = ["claude"]
    renderer.manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    codex = mcp_check.check_gateway(renderer, "codex", timeout=30)
    assert codex.ok is False
    assert "codex" in codex.problems[0] and "good" in codex.problems[0]  # the wording follows the locale


def test_a_gateway_that_never_answers_is_a_failure_not_a_hang(setup):
    renderer, _ = setup
    manifest = yaml.safe_load(renderer.manifest_path.read_text())
    manifest["servers"]["lazy-mcp"]["args"] = ["-c", "import time; time.sleep(60)"]
    renderer.manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    started = time.monotonic()
    result = mcp_check.check_gateway(renderer, "codex", timeout=3)
    assert result.ok is False
    assert time.monotonic() - started < 45
    assert any("initialize" in p for p in result.problems), result.problems  # said in the user's language


def test_the_command_prints_and_exits_nonzero_on_a_failure(setup, capsys, monkeypatch):
    renderer, _ = setup
    monkeypatch.setattr("nexgen_core.renderer.McpRenderer", lambda *a, **k: renderer)
    code = mcp_check.main(("claude", "codex"), timeout=60)
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL claude" in out and "OK   codex" in out


def test_when_the_cli_goes_away_the_gateway_and_its_backends_go_with_it(setup, tmp_path):
    """A CLI that crashes closes the gateway's stdin. Nothing may be left running: no force-kill helps here."""
    import subprocess

    renderer, pidfile = setup
    entry = renderer.load_resolved_servers("codex")["lazy-mcp"]
    proc = subprocess.Popen([entry["command"], *entry["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env={**os.environ, **entry["env"]})
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "lazy_list", "arguments": {}}}) + "\n")
        proc.stdin.flush()
        assert "good" in proc.stdout.readline()
        backend = int(pidfile.read_text())
        assert alive(backend)
        proc.stdin.close()  # the CLI is gone
        proc.wait(timeout=15)
        for _ in range(50):
            if not alive(backend):
                break
            time.sleep(0.1)
        assert not alive(backend), "the backend outlived a gateway whose CLI went away"
    finally:
        if proc.poll() is None:
            proc.kill()


# --- the directly mounted servers: what they cost a CLI that loads them up front --------------------------

def _http_server(tools):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen = {"auth": [], "sessions": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["auth"].append(self.headers.get("Authorization"))
            seen["sessions"].append(self.headers.get("Mcp-Session-Id"))
            if body["method"] == "initialize":
                result, extra = {"protocolVersion": "2025-06-18", "capabilities": {}}, {"Mcp-Session-Id": "s1"}
            else:
                result, extra = {"tools": tools}, {}
            payload = "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}) + "\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_a_direct_stdio_server_is_measured(setup):
    renderer, _ = setup
    manifest = yaml.safe_load(renderer.manifest_path.read_text())
    manifest["servers"]["good"]["exposure"] = "eager"
    renderer.manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    outcome = mcp_check.check_direct(renderer, "codex", "good", timeout=30)
    assert outcome.ok, outcome.problem
    assert outcome.tools == 1 and outcome.tokens > 0


def test_a_direct_http_server_is_measured_with_its_bearer_and_session(setup, monkeypatch):
    renderer, _ = setup
    tools = [{"name": f"t{i}", "description": "d" * 80, "inputSchema": {"type": "object"}} for i in range(5)]
    server, seen = _http_server(tools)
    try:
        manifest = yaml.safe_load(renderer.manifest_path.read_text())
        manifest["servers"]["remote"] = {"exposure": "eager", "transport": "http", "url": f"http://127.0.0.1:{server.server_port}/mcp",
                                         "auth": {"type": "bearer", "env": "REMOTE_TOKEN"}}
        renderer.manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
        missing = mcp_check.check_direct(renderer, "claude", "remote", timeout=10)
        assert missing.ok is False and "REMOTE_TOKEN" in missing.problem
        monkeypatch.setenv("REMOTE_TOKEN", "t0ken")
        outcome = mcp_check.check_direct(renderer, "claude", "remote", timeout=10)
        assert outcome.ok, outcome.problem
        assert outcome.tools == 5 and outcome.tokens > 50
        assert set(seen["auth"]) == {"Bearer t0ken"}
        assert seen["sessions"][-1] == "s1"
    finally:
        server.shutdown()


def test_the_direct_summary_totals_what_each_cli_loads_up_front(setup, capsys, monkeypatch):
    renderer, _ = setup
    manifest = yaml.safe_load(renderer.manifest_path.read_text())
    manifest["servers"]["good"]["exposure"] = "eager"
    renderer.manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr("nexgen_core.renderer.McpRenderer", lambda *a, **k: renderer)
    assert mcp_check.main_direct(("claude", "codex"), timeout=30) == 0
    out = capsys.readouterr().out
    assert "claude: 1" in out and "codex: 1" in out and "OK   good" in out


def _rewrite(renderer, tmp_path, extra):
    manifest = yaml.safe_load(renderer.manifest_path.read_text(encoding="utf-8"))
    manifest["servers"].update(extra)
    renderer.manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")


def test_verify_passes_a_working_server_and_names_a_broken_one_behind_the_gateway(setup):
    renderer, _ = setup
    assert mcp_check.verify_servers(renderer, ["good"], timeout=60) == []
    problems = mcp_check.verify_servers(renderer, ["broken"], timeout=60)
    assert problems and problems[0].startswith("broken:") and "missing_dep" in problems[0]


def test_verify_starts_a_directly_mounted_server_on_its_own(setup, tmp_path):
    renderer, _ = setup
    _rewrite(renderer, tmp_path, {
        "direct-good": {"exposure": "eager", "command": sys.executable, "args": ["-c", GOOD],
                        "env": {"PIDFILE": str(tmp_path / "d.pid")}, "targets": ["claude"]},
        "direct-dead": {"exposure": "eager", "command": sys.executable, "args": ["-c", DYING], "targets": ["claude"]},
    })
    assert mcp_check.verify_servers(renderer, ["direct-good"], timeout=60) == []
    problems = mcp_check.verify_servers(renderer, ["direct-dead"], timeout=60)
    assert len(problems) == 1 and problems[0].startswith("direct-dead:")


def test_verify_ignores_a_name_the_manifest_does_not_have(setup):
    renderer, _ = setup
    assert mcp_check.verify_servers(renderer, ["nothing-by-that-name"], timeout=5) == []


def test_verify_does_not_blame_an_update_for_a_credential_the_session_lacks(setup, tmp_path, monkeypatch):
    renderer, _ = setup
    monkeypatch.delenv("NEEDY_TEST_TOKEN", raising=False)
    _rewrite(renderer, tmp_path, {
        "needy": {"exposure": "lazy", "command": sys.executable, "args": ["-c", GOOD],
                  "env": {"PIDFILE": str(tmp_path / "n.pid"), "NEEDY_TEST_TOKEN": "${NEEDY_TEST_TOKEN}"}, "targets": ["codex"]},
    })
    plain = mcp_check.check_gateway(renderer, "codex", timeout=60)
    assert "needy" in plain.unavailable
    assert mcp_check.verify_servers(renderer, ["needy"], timeout=60) == []
