"""Test del waiter lazy-mcp: indice, load, forward, gate mutating FAIL-CLOSED.

Kimi review (BLOCKER): il default di classificazione deve essere mutating.
Un tool senza allowlist esplicita richiede conferma, indipendentemente dalle
MCP tool annotations (advisory, da server non fidati). Il fake upstream qui
espone tool SENZA annotations: e' il caso che il gate deve coprire.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LAZY_MCP = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "mcp" / "lazy-mcp.py"

FAKE_SERVER = r"""
import json, sys
TOOLS = [
  {"name": "read_thing", "description": "reads a value", "inputSchema": {"type": "object", "properties": {}}},
  {"name": "write_thing", "description": "writes a value", "inputSchema": {"type": "object", "properties": {"v": {"type": "string"}}}},
]
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    m, rid = req.get("method"), req.get("id")
    if m in ("initialize", "notifications/initialized", "ping"):
        continue
    if m == "server/discover":
        out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "supportedVersions": ["2026-07-28"], "capabilities": {"tools": {"listChanged": False}}}}
    elif m == "tools/list":
        out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": TOOLS}}
    elif m == "tools/call":
        p = req["params"]
        if p["name"] == "write_thing":
            out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "written:" + p["arguments"].get("v", "")}]}}
        else:
            out = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "value:42"}]}}
    else:
        out = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "nf"}}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
"""


def _write_manifest(vault: Path, body: str) -> None:
    d = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(body, encoding="utf-8")


def _spawn_waiter(vault: Path, audit: Path):
    env = dict(os.environ, AGENT_VAULT_DATA=str(vault), LAZY_MCP_LOG=str(audit))
    return subprocess.Popen(
        ["python3", str(LAZY_MCP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env=env,
    )


def _rpc(proc, method: str, params=None, rid: int = 1):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    time.sleep(0.4)
    return json.loads(proc.stdout.readline().strip())


def _base_manifest(fake_cmd: str) -> str:
    return f"""
schema_version: 1
retired_servers: []
servers:
  fake:
    lazy: true
    command: python3
    args: ["-c", {json.dumps(fake_cmd)}]
    targets: [claude, codex, antigravity, opencode]
"""


def _start_fake(manifest_fragment: str, tmp_path: Path, audit: Path):
    vault = tmp_path / "vault"
    _write_manifest(vault, manifest_fragment)
    proc = _spawn_waiter(vault, audit)
    return proc


def _call(proc, server: str, tool: str, arguments=None, confirm: bool | None = None, rid: int = 10):
    args = {"server": server, "tool": tool, "arguments": arguments or {}}
    if confirm is not None:
        args["confirm"] = confirm
    r = _rpc(proc, "tools/call", {"name": "lazy_call", "arguments": args}, rid=rid)
    return r["result"]


def test_fail_closed_without_any_annotation(tmp_path: Path):
    """Tool SENZA annotations: senza allowlist, la chiamata e rifiutata (fail-closed)."""
    audit = tmp_path / "audit.jsonl"
    proc = _start_fake(_base_manifest(FAKE_SERVER), tmp_path, audit)
    try:
        # anche un tool dal nome read-* e' mutating di default
        res = _call(proc, "fake", "read_thing")
        assert res.get("isError") is True, res
        assert "requires confirmation" in res["content"][0]["text"]
        res = _call(proc, "fake", "write_thing")
        assert res.get("isError") is True
    finally:
        proc.kill()
    log = audit.read_text(encoding="utf-8")
    # il rifiuto va in audit come refused, mai come call eseguita
    assert '"action": "refused"' in log
    assert '"action": "call"' not in log


def test_readonly_allowlist_passes_without_confirm(tmp_path: Path):
    """Tool allowlistato read-only: passa senza conferma; gli altri no."""
    audit = tmp_path / "audit.jsonl"
    manifest = _base_manifest(FAKE_SERVER).replace(
        "  fake:\n    lazy: true",
        "  fake:\n    lazy: true\n    readonly_tools: [read_thing]",
    )
    proc = _start_fake(manifest, tmp_path, audit)
    try:
        res = _call(proc, "fake", "read_thing")
        assert res.get("isError") is not True, res
        assert res["content"][0]["text"] == "value:42"
        res = _call(proc, "fake", "write_thing")
        assert res.get("isError") is True, "write_thing non allowlistato deve essere rifiutato"
    finally:
        proc.kill()
    log = audit.read_text(encoding="utf-8")
    assert '"action": "call"' in log
    assert '"tool": "read_thing"' in log


def test_mutating_with_confirm_executes_and_audits(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    proc = _start_fake(_base_manifest(FAKE_SERVER), tmp_path, audit)
    try:
        res = _call(proc, "fake", "write_thing", {"v": "ciao"}, confirm=True)
        assert res.get("isError") is not True, res
        assert res["content"][0]["text"] == "written:ciao"
    finally:
        proc.kill()
    log = audit.read_text(encoding="utf-8")
    assert '"confirmed": true' in log


def test_index_lists_servers_and_tools(tmp_path: Path):
    proc = _start_fake(_base_manifest(FAKE_SERVER), tmp_path, tmp_path / "audit.jsonl")
    try:
        r = _rpc(proc, "tools/call", {"name": "lazy_list", "arguments": {}}, rid=2)
        idx = json.loads(r["result"]["content"][0]["text"])
        assert "fake" in idx["servers"]
        names = [t["name"] for t in idx["servers"]["fake"]["tools"]]
        assert names == ["read_thing", "write_thing"]
        r = _rpc(proc, "tools/call", {"name": "lazy_load", "arguments": {"server": "fake", "tool": "write_thing"}}, rid=3)
        assert "inputSchema" in r["result"]["content"][0]["text"]
    finally:
        proc.kill()


def test_engine_root_placeholder_expansion(tmp_path: Path):
    """Test that ${AGENT_ENGINE_ROOT} is resolved automatically without env var."""
    audit = tmp_path / "audit.jsonl"
    manifest = """
schema_version: 1
retired_servers: []
servers:
  root_test:
    lazy: true
    readonly: true
    command: python3
    args: ["${AGENT_ENGINE_ROOT}/deploy/ocr/mcp/vault_ocr_mcp.py"]
    targets: [antigravity, codex]
"""
    vault = tmp_path / "vault"
    _write_manifest(vault, manifest)
    env = dict(os.environ, AGENT_VAULT_DATA=str(vault), LAZY_MCP_LOG=str(audit))
    env.pop("AGENT_ENGINE_ROOT", None)
    proc = subprocess.Popen(
        ["python3", str(LAZY_MCP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env=env,
    )
    try:
        r = _rpc(proc, "tools/call", {"name": "lazy_list", "arguments": {}}, rid=2)
        idx = json.loads(r["result"]["content"][0]["text"])
        assert "root_test" in idx["servers"]
        tools = idx["servers"]["root_test"]["tools"]
        names = [t["name"] for t in tools]
        assert "ocr_extract_image" in names
    finally:
        proc.kill()


def test_fast_failure_on_crashing_server(tmp_path: Path):
    """A crashing child server must fail immediately rather than hanging for 90s."""
    audit = tmp_path / "audit.jsonl"
    manifest = """
schema_version: 1
retired_servers: []
servers:
  broken:
    lazy: true
    readonly: true
    command: python3
    args: ["-c", "import sys; sys.exit(42)"]
    targets: [antigravity]
"""
    t0 = time.time()
    proc = _start_fake(manifest, tmp_path, audit)
    try:
        r = _rpc(proc, "tools/call", {"name": "lazy_call", "arguments": {"server": "broken", "tool": "any"}}, rid=2)
        elapsed = time.time() - t0
        assert elapsed < 5.0, f"Call took too long ({elapsed}s), likely hung in loop"
        assert r.get("result", {}).get("isError") is True
    finally:
        proc.kill()


def test_unreachable_http_endpoint(tmp_path: Path):
    """An unreachable HTTP server must return an error without crashing the proxy."""
    audit = tmp_path / "audit.jsonl"
    manifest = """
schema_version: 1
retired_servers: []
servers:
  offline_http:
    lazy: true
    readonly: true
    transport: http
    url: "http://127.0.0.1:59999/mcp"
    targets: [antigravity]
"""
    proc = _start_fake(manifest, tmp_path, audit)
    try:
        r = _rpc(proc, "tools/call", {"name": "lazy_list", "arguments": {}}, rid=2)
        idx = json.loads(r["result"]["content"][0]["text"])
        assert "offline_http" in idx["servers"]
        assert idx["servers"]["offline_http"]["tools"] == []
    finally:
        proc.kill()



def test_git_dep_provisioned_at_spawn_with_workspace_substitution(tmp_path: Path):
    """Dep git pinnata: il cameriere la provvede al primo spawn e sostituisce
    DEPS_WORKSPACE in args e env (regressione: il token veniva espanso a
    stringa vuota al caricamento del manifest e il server non partiva)."""
    audit = tmp_path / "audit.jsonl"
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True)
    server_py = FAKE_SERVER.replace('"value:42"', '"value:"+os.environ.get("FAKE_WS","")')
    (src / "fake_ws_mcp.py").write_text("import os\n" + server_py, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=src, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=src, check=True)
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True, capture_output=True, text=True).stdout.strip()

    manifest = f"""
schema_version: 1
retired_servers: []
servers:
  fakews:
    lazy: true
    command: python3
    args: ["${{DEPS_WORKSPACE}}/fake_ws_mcp.py"]
    env: {{FAKE_WS: "${{DEPS_WORKSPACE}}"}}
    readonly_tools: [read_thing]
    deps: {{kind: git, repo: "{src.as_uri()}", rev: "{rev}"}}
"""
    proc = _start_fake(manifest, tmp_path, audit)
    try:
        res = _call(proc, "fakews", "read_thing", rid=11)
        assert res.get("isError") is not True, res
        text = res["content"][0]["text"]
        assert text.startswith("value:"), text
        ws = text[len("value:"):]
        assert (Path(ws) / "fake_ws_mcp.py").is_file(), f"workspace {ws} non provisionato"
    finally:
        proc.kill()
    # la verifica offline (install=False) lo conferma dopo lo spawn
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from nexgen_core.provision import ensure_deps
    ctx, err = ensure_deps(
        {"kind": "git", "repo": src.as_uri(), "rev": rev},
        Path.home() / ".local" / "state", install=False, server="fakews",
    )
    assert err is None, err
    assert ctx["DEPS_WORKSPACE"] == ws


def test_the_index_carries_the_four_hints_when_the_server_declares_them(tmp_path: Path):
    """Le annotation dei tool upstream attraversano il proxy nell'indice.

    Advisory: restano etichette per chi chiama, mai una via di bypass del
    gate fail-closed, che continua a guardare solo readonly/readonly_tools.
    """
    fake_with_hints = FAKE_SERVER.replace(
        '"read_thing", "description"',
        '"read_thing", "annotations": {"readOnlyHint": True, "destructiveHint": False, '
        '"idempotentHint": True, "openWorldHint": True}, "description"',
    )
    audit = tmp_path / "audit.jsonl"
    manifest = _base_manifest(fake_with_hints)
    proc = _start_fake(manifest, tmp_path, audit)
    try:
        r = _rpc(proc, "tools/call", {"name": "lazy_list", "arguments": {}}, rid=21)
        payload = json.loads(r["result"]["content"][0]["text"])
        tools = {t["name"]: t for t in payload["servers"]["fake"]["tools"]}
        assert tools["read_thing"].get("annotations") == {
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        }
        # il tool senza annotations resta senza la chiave: nessun default inventato
        assert "annotations" not in tools["write_thing"]
    finally:
        proc.kill()


def test_the_meta_tools_expose_their_own_hints(tmp_path: Path):
    """lazy_list/lazy_load si dichiarano di sola lettura, lazy_call no."""
    audit = tmp_path / "audit.jsonl"
    proc = _start_fake(_base_manifest(FAKE_SERVER), tmp_path, audit)
    try:
        r = _rpc(proc, "tools/list", rid=22)
        tools = {t["name"]: t for t in r["result"]["tools"]}
        assert tools["lazy_list"]["annotations"]["readOnlyHint"] is True
        assert tools["lazy_load"]["annotations"]["readOnlyHint"] is True
        assert tools["lazy_call"]["annotations"]["readOnlyHint"] is False
        assert tools["lazy_call"]["annotations"]["destructiveHint"] is True
    finally:
        proc.kill()


def test_the_waiter_expands_the_same_inline_templates(tmp_path: Path):
    """Il cameriere e il renderer parlano lo stesso dialetto: un server
    definito una volta si comporta uguale su entrambe le vie."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("lazy_mcp_under_test", LAZY_MCP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    ctx = {"os": "windows", "home": "C:\\Users\\u", "vault": "C:\\v", "engine": "C:\\e"}
    text = '{{ if eq .os "windows" }}C:\\ws{{ else }}/tmp/ws{{ end }}'
    real_ctx = mod._template_context
    mod._template_context = lambda: ctx
    try:
        assert mod._expand_templates(text) == "C:\\ws"
    finally:
        mod._template_context = real_ctx

    # sul contesto reale del processo il ramo giusto è quello dell'host
    real = mod._expand_templates(text)
    assert real == ("C:\\ws" if real_ctx()["os"] == "windows" else "/tmp/ws")

    # un template che non sa onorare NON passa attraverso: TemplateError
    # propaga, e il chiamante ritira il server invece di fare spawn di {{ }}
    try:
        mod._expand_templates("{{ .non_esisto }}")
        raised = False
    except Exception:
        raised = True
    assert raised, "un template rotto deve fallire, non arrivare allo spawn"


def test_the_waiter_template_context_tracks_the_host(tmp_path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("lazy_mcp_under_test_ctx", LAZY_MCP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    ctx = mod._template_context()
    assert ctx["os"] in ("linux", "darwin", "windows")
    assert ctx["os"] == ("windows" if sys.platform == "win32" else ("darwin" if sys.platform == "darwin" else "linux"))


def test_a_lazy_server_with_a_broken_template_is_withdrawn(tmp_path: Path, monkeypatch):
    """Fail-closed per entry: il server col template rotto esce dall'indice
    con il motivo su stderr, e il resto dell'indice resta in piedi."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("lazy_mcp_withdrawn", LAZY_MCP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    manifest = (
        "schema_version: 1\n"
        "servers:\n"
        "  rottoserv:\n"
        "    lazy: true\n"
        '    command: python3\n'
        '    args: ["{{ .non_esisto }}"]\n'
        "  buonoserv:\n"
        "    lazy: true\n"
        "    command: echo\n"
    )
    _write_manifest(tmp_path, manifest)
    # il cameriere risolve il manifest da AGENT_VAULT_DATA: nel test, la sandbox
    monkeypatch.setenv("AGENT_VAULT_DATA", str(tmp_path))

    servers = mod._lazy_servers()
    assert "rottoserv" not in servers, "il server col template rotto non deve arrivare allo spawn"
    assert "buonoserv" in servers, "l\'indice non crolla per una entry sola"
