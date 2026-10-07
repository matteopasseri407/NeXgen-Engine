"""Where a server's credential comes from when the CLI was not started from a shell that exported it.

The deposit (`nexgen-secrets materialize`) writes `~/.config/nexgen/secrets.env`, but nothing loads that file into a
launcher-started CLI, so a token that was safely stored still never reached "Vercel" or "Supabase". The gateway now reads
the credentials a server's entry names from that file, and nothing else from it. These tests use made-up values only.
"""
from __future__ import annotations

import http.server
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))
LAZY = INFRA / "agent-universal-layer" / "mcp" / "lazy-mcp.py"

from nexgen_core import deposit_env  # noqa: E402
from nexgen_core.checks.security_checks import check_tokens_in_env  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes and process semantics")

DEPOSIT = """# generated, do not edit
export FIRST_TOKEN='first-from-deposit'
export QUOTED_ONE="two words"
export MULTI='line one
line two'
PLAIN_ONE=plain
export WITH_QUOTE='it'"'"'s fine'
"""

#: A tiny server that records the credential it was started with, so the test can see what reached it.
ECHO_ENV = r"""
import json, os, sys
open(os.environ["SEEN_FILE"], "w").write(os.environ.get("SVC_API_TOKEN", "<absent>") + "|" + os.environ.get("SVC_COMPOSED", "<absent>"))
TOOLS = [{"name": "ping_tool", "description": "pong", "inputSchema": {"type": "object", "properties": {}}}]
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        continue
    result = {"tools": TOOLS} if method == "tools/list" else {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n"); sys.stdout.flush()
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(tmp_path / "vault"))
    monkeypatch.setenv("NEXGEN_SECRETS_ENV", str(tmp_path / "secrets.env"))
    for name in ("SVC_API_TOKEN", "SVC_COMPOSED", "FIRST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    deposit_env._CACHE = ((), {})
    return tmp_path


def write_deposit(home, text=DEPOSIT, mode=0o600):
    path = home / "secrets.env"
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    deposit_env._CACHE = ((), {})
    return path


# --- the reader ----------------------------------------------------------------------------------------

@posix_only
def test_the_reader_understands_what_materialize_writes(home):
    write_deposit(home)
    values = deposit_env.read_deposit()
    assert values["FIRST_TOKEN"] == "first-from-deposit"
    assert values["QUOTED_ONE"] == "two words"
    assert values["MULTI"] == "line one\nline two"
    assert values["PLAIN_ONE"] == "plain"
    assert values["WITH_QUOTE"] == "it's fine"


@posix_only
@pytest.mark.parametrize("mode", [0o666, 0o662, 0o626])
def test_a_file_anyone_else_can_write_is_not_believed(home, mode):
    write_deposit(home, mode=mode)
    assert deposit_env.read_deposit() == {}


@posix_only
def test_a_file_only_others_can_read_is_still_the_users_own_to_use(home):
    write_deposit(home, mode=0o644)
    assert deposit_env.read_deposit()["FIRST_TOKEN"] == "first-from-deposit"


@posix_only
def test_an_open_quote_makes_the_whole_file_untrustworthy(home):
    write_deposit(home, "export GOOD='fine'\nexport BAD='never closed\n")
    assert deposit_env.read_deposit() == {}


def test_no_file_means_nothing(home):
    assert deposit_env.read_deposit() == {}


# --- the gateway ---------------------------------------------------------------------------------------

def gateway(monkeypatch, cli="claude"):
    monkeypatch.setenv("LAZY_MCP_CLI", cli)
    spec = importlib.util.spec_from_file_location("lazy_for_credentials_tests", LAZY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_manifest(home, servers):
    path = home / "vault" / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"servers": {"lazy-mcp": {"tier": "core", "command": "x"}, **servers}}), encoding="utf-8")


@posix_only
def test_a_credential_is_found_process_first_then_the_session_then_the_deposit(home, monkeypatch):
    write_deposit(home)
    module = gateway(monkeypatch)
    assert module._declared_value("FIRST_TOKEN") == "first-from-deposit"
    conf = home / "xdg" / "environment.d"
    conf.mkdir(parents=True)
    (conf / "90.conf").write_text("FIRST_TOKEN=from-the-session\n", encoding="utf-8")
    module._MACHINE_ENV_CACHE = ((), {})
    assert module._declared_value("FIRST_TOKEN") == "from-the-session"
    monkeypatch.setenv("FIRST_TOKEN", "from-the-process")
    assert module._declared_value("FIRST_TOKEN") == "from-the-process"


@posix_only
def test_a_server_needing_a_token_only_the_deposit_has_is_available_and_is_given_it(home, monkeypatch):
    write_deposit(home, "export SVC_API_TOKEN='deposit-value-1'\n")
    seen = home / "seen"
    write_manifest(home, {"svc": {"exposure": "lazy", "command": sys.executable, "args": ["-c", ECHO_ENV],
                                  "env": {"SEEN_FILE": str(seen), "SVC_API_TOKEN": "${SVC_API_TOKEN}"}}})
    module = gateway(monkeypatch)
    assert "_unavailable" not in module._lazy_servers()["svc"]
    waiter = module.Waiter()
    try:
        index = waiter.index()
    finally:
        waiter.shutdown()
    assert [t["name"] for t in index["servers"]["svc"]["tools"]] == ["ping_tool"]
    assert seen.read_text(encoding="utf-8").split("|")[0] == "deposit-value-1"
    assert "deposit-value-1" not in json.dumps(index)


@posix_only
def test_only_a_plain_reference_reads_the_deposit_never_a_composed_value(home, monkeypatch):
    write_deposit(home, "export SVC_API_TOKEN='deposit-value-2'\n")
    seen = home / "seen"
    write_manifest(home, {"svc": {"exposure": "lazy", "command": sys.executable, "args": ["-c", ECHO_ENV],
                                  "env": {"SEEN_FILE": str(seen), "SVC_COMPOSED": "prefix-${SVC_API_TOKEN}"}}})
    module = gateway(monkeypatch)
    waiter = module.Waiter()
    try:
        waiter.index()
    finally:
        waiter.shutdown()
    assert seen.read_text(encoding="utf-8").split("|")[1] == "prefix-"


@posix_only
def test_a_credential_in_neither_place_is_named_as_missing(home, monkeypatch):
    write_deposit(home, "export SOMETHING_ELSE='x'\n")
    write_manifest(home, {"svc": {"exposure": "lazy", "command": sys.executable, "args": ["-c", ECHO_ENV],
                                  "env": {"SEEN_FILE": str(home / "seen"), "SVC_API_TOKEN": "${SVC_API_TOKEN}"}}})
    entry = gateway(monkeypatch)._lazy_servers()["svc"]
    assert "SVC_API_TOKEN" in entry["_unavailable"]


class _Recorder(http.server.BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_POST(self):  # noqa: N802 - http.server API
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Recorder.seen.append(self.headers.get("Authorization", ""))
        result = {"tools": [{"name": "remote_tool", "description": "d", "inputSchema": {"type": "object"}}]} if body.get("method") == "tools/list" else {}
        payload = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@posix_only
def test_a_bearer_token_for_an_http_server_comes_from_the_deposit_too(home, monkeypatch):
    write_deposit(home, "export SVC_API_TOKEN='deposit-bearer'\n")
    _Recorder.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        write_manifest(home, {"remote": {"exposure": "lazy", "transport": "http", "readonly": True,
                                         "url": f"http://127.0.0.1:{server.server_address[1]}/mcp",
                                         "auth": {"type": "bearer", "env": "SVC_API_TOKEN"}}})
        module = gateway(monkeypatch)
        waiter = module.Waiter()
        try:
            index = waiter.index()
        finally:
            waiter.shutdown()
    finally:
        server.shutdown()
    assert [t["name"] for t in index["servers"]["remote"]["tools"]] == ["remote_tool"]
    assert _Recorder.seen and set(_Recorder.seen) == {"Bearer deposit-bearer"}


# --- the doctor ----------------------------------------------------------------------------------------

def manifest_with(home, **entry):
    path = home / "vault" / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"servers": {"remote": {"transport": "http", "url": "http://127.0.0.1:9/mcp",
                                                           "auth": {"type": "bearer", "env": "SVC_API_TOKEN"}, **entry}}}), encoding="utf-8")
    return home / "vault"


@posix_only
def test_the_doctor_accepts_a_deposit_token_for_a_server_behind_the_gateway(home):
    write_deposit(home, "export SVC_API_TOKEN='deposit-value-3'\n")
    assert check_tokens_in_env(manifest_with(home, exposure="lazy")).severity is Severity.OK


@posix_only
def test_the_doctor_still_wants_the_real_environment_for_a_directly_mounted_server(home):
    write_deposit(home, "export SVC_API_TOKEN='deposit-value-3'\n")
    outcome = check_tokens_in_env(manifest_with(home, exposure="eager"))
    assert outcome.severity is Severity.BROKEN and "SVC_API_TOKEN" in outcome.message


@posix_only
def test_the_doctor_reports_a_token_that_is_nowhere(home):
    assert check_tokens_in_env(manifest_with(home, exposure="lazy")).severity is Severity.BROKEN
