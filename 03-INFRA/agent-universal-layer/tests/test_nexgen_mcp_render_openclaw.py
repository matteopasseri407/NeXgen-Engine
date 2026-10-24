"""OpenClaw as a first-class MCP target: what actually lands in its own file.

Every test here runs the real renderer against a temporary `openclaw.json`
and reads the JSON that comes out, because the three rules that make OpenClaw
different (resolved URL, explicit `streamable-http`, UTF-8 for Python
children) are exactly the ones a schema refuses or a phantom timeout hides.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.paths import openclaw_config  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402

MANIFEST = """schema_version: 1
retired_servers: [old-server]
servers:
  py-tool:
    tier: core
    transport: stdio
    command: python3
    args: ["-m", "demo"]
    env: {DEMO_MODE: headless}
  node-tool:
    tier: core
    transport: stdio
    command: node
    args: ["server.mjs"]
  secret-tool:
    tier: core
    transport: stdio
    command: node
    args: ["secret.mjs"]
    env:
      DEMO_SECRET: "${DEMO_SECRET}"
      AWS_ACCESS_KEY_ID: "${AWS_ACCESS_KEY_ID}"
      DATABASE_URL: "${DATABASE_URL}"
      DEMO_PLAIN: plain
  path-tool:
    tier: core
    transport: stdio
    command: node
    args: ["path.mjs"]
    env:
      VAULT_PATH: "${AGENT_VAULT_DATA}"
  remote:
    tier: core
    transport: http
    url: "${DEMO_URL}/mcp"
    auth: {type: bearer, env: DEMO_TOKEN}
    timeouts: {startup: 12, tool: 90}
  unresolved:
    tier: core
    transport: http
    url: "${MISSING_URL_FOR_TESTS}"
  trimmed:
    tier: core
    transport: stdio
    command: npx
    args: ["-y", "demo-mcp@1.0.0"]
    tools_deny: [expensive_tool]
"""

#: What OpenClaw itself keeps in the same file: untouched by every test.
USER_FILE = {
    "gateway": {"port": 18789, "bind": "loopback"},
    "agents": {"main": {"model": "anthropic/claude"}},
    "plugins": {"allow": ["browser", "nexgen"], "entries": {"nexgen": {"enabled": True}}},
    "mcp": {
        "servers": {
            "my-own-server": {"command": "node", "args": ["mine.mjs"]},
            "old-server": {"command": "node", "args": ["retired.mjs"]},
        }
    },
}


@pytest.fixture
def renderer(tmp_path: Path, monkeypatch) -> McpRenderer:
    """A renderer over a synthetic vault, with OpenClaw's location overrides cleared."""
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    monkeypatch.setenv("DEMO_URL", "https://demo.invalid")
    monkeypatch.setenv("DEMO_TOKEN", "tok-should-never-be-written")
    monkeypatch.setenv("DEMO_SECRET", "super-secret-value")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-SECRET-EXAMPLE")
    monkeypatch.setenv("DATABASE_URL", "postgres://example.invalid/db")
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(MANIFEST, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


def _install_openclaw(renderer: McpRenderer) -> Path:
    """The file OpenClaw would already have on a machine where it is installed."""
    cfg = openclaw_config(renderer.home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(USER_FILE, indent=2), encoding="utf-8")
    return cfg


def _servers(renderer: McpRenderer) -> dict:
    cfg = openclaw_config(renderer.home)
    return json.loads(cfg.read_text(encoding="utf-8"))["mcp"]["servers"]


def test_a_python_server_gets_a_utf8_environment_and_other_commands_do_not(renderer) -> None:
    _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    servers = _servers(renderer)
    py = servers["py-tool"]
    assert py["env"]["PYTHONUTF8"] == "1"
    assert py["env"]["PYTHONIOENCODING"] == "utf-8"
    assert py["env"]["DEMO_MODE"] == "headless", "the manifest's own environment is kept"
    assert py["args"] == ["-m", "demo"]
    assert py["command"] in ("python3", "python"), "python3 is spelled python on Windows"
    assert "PYTHONUTF8" not in servers["node-tool"].get("env", {})


def test_an_http_server_carries_a_resolved_url_and_the_transport(renderer) -> None:
    _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    remote = _servers(renderer)["remote"]
    assert remote["url"] == "https://demo.invalid/mcp", "OpenClaw validates the URL before substituting ${VAR}"
    assert remote["transport"] == "streamable-http", "without it the handshake times out"
    assert "${" not in remote["url"]


def test_a_secret_stays_an_environment_reference(renderer) -> None:
    cfg = _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    assert _servers(renderer)["remote"]["headers"] == {"Authorization": "Bearer ${DEMO_TOKEN}"}
    assert "tok-should-never-be-written" not in cfg.read_text(encoding="utf-8")


def test_a_reference_in_a_stdio_environment_stays_a_reference(renderer) -> None:
    """The generic renderer expands `${VAR}`; OpenClaw substitutes it itself.

    Writing the expanded value copied the secret out of the environment and
    into `openclaw.json`, which the engine otherwise never fills with secrets.
    """
    cfg = _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    env = _servers(renderer)["secret-tool"]["env"]
    assert env["DEMO_SECRET"] == "${DEMO_SECRET}"
    assert env["DEMO_PLAIN"] == "plain"
    assert "super-secret-value" not in cfg.read_text(encoding="utf-8")


def test_cloud_credentials_with_unusual_names_also_stay_references(renderer) -> None:
    """A name heuristic let AWS keys and database URLs through; the rule is now the opposite."""
    cfg = _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    env = _servers(renderer)["secret-tool"]["env"]
    assert env["AWS_ACCESS_KEY_ID"] == "${AWS_ACCESS_KEY_ID}"
    assert env["DATABASE_URL"] == "${DATABASE_URL}"
    rendered = cfg.read_text(encoding="utf-8")
    assert "AKIA-SECRET-EXAMPLE" not in rendered
    assert "example.invalid/db" not in rendered


def test_a_non_secret_reference_is_still_resolved(renderer) -> None:
    """A path must not depend on the gateway's own environment, which is not the engine's."""
    _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    env = _servers(renderer)["path-tool"]["env"]
    assert env["VAULT_PATH"] == str(renderer.vault_data)
    assert "${" not in env["VAULT_PATH"]


def test_the_mcp_renderer_never_touches_the_plugins_block(renderer) -> None:
    """Enabling a plugin is `nexgen openclaw setup`'s job, with the gateway stopped."""
    cfg = _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["plugins"] == USER_FILE["plugins"]


def test_timeouts_become_milliseconds(renderer) -> None:
    _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    remote = _servers(renderer)["remote"]
    assert remote["connectionTimeoutMs"] == 12000
    assert remote["requestTimeoutMs"] == 90000


def test_a_server_whose_url_variable_is_missing_is_skipped(renderer) -> None:
    _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    servers = _servers(renderer)
    assert "unresolved" not in servers
    assert all("${MISSING_URL_FOR_TESTS}" not in json.dumps(entry) for entry in servers.values())


def test_tools_deny_becomes_a_tool_filter(renderer) -> None:
    _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    trimmed = _servers(renderer)["trimmed"]
    assert trimmed["toolFilter"] == {"exclude": ["expensive_tool"]}
    # It hides tools, so every CLI mounts it through the one trimming shim.
    assert any("mcp-trim.py" in str(arg) for arg in trimmed["args"])


def test_a_retired_server_disappears_and_the_rest_of_the_file_is_kept(renderer) -> None:
    cfg = _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "old-server" not in data["mcp"]["servers"], "retired_servers is the removal mechanism"
    assert data["mcp"]["servers"]["my-own-server"] == USER_FILE["mcp"]["servers"]["my-own-server"]
    assert data["gateway"] == USER_FILE["gateway"]
    assert data["agents"] == USER_FILE["agents"]


def test_no_file_is_created_where_openclaw_is_not_installed(renderer) -> None:
    cfg = openclaw_config(renderer.home)
    assert not cfg.exists()

    assert renderer.render_openclaw(write=True)[0]
    assert not cfg.exists(), "the engine patches configurations that exist"
    assert not cfg.parent.exists()


def test_a_second_render_without_changes_leaves_the_file_alone(renderer) -> None:
    cfg = _install_openclaw(renderer)
    assert renderer.render_openclaw(write=True)[0]
    first = cfg.read_bytes()
    backups_after_first = sorted(p.name for p in cfg.parent.glob("*.bak-*"))

    assert renderer.render_openclaw(write=True)[0]
    assert cfg.read_bytes() == first
    assert sorted(p.name for p in cfg.parent.glob("*.bak-*")) == backups_after_first, (
        "a render that changes nothing must not back the file up again"
    )


def test_render_all_covers_openclaw(renderer) -> None:
    _install_openclaw(renderer)

    results = renderer.render_all(write=True)

    assert results["openclaw"] is True, results
    assert "py-tool" in _servers(renderer)


def test_a_config_without_an_mcp_block_still_gets_its_servers(renderer) -> None:
    """A fresh OpenClaw config has no `mcp` table at all: that is not an error."""
    cfg = openclaw_config(renderer.home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"gateway": {"port": 18789}}), encoding="utf-8")

    assert renderer.render_openclaw(write=True)[0]

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "py-tool" in data["mcp"]["servers"]
    assert data["gateway"] == {"port": 18789}


def test_the_openclaw_server_is_never_rendered_into_openclaw_itself(renderer) -> None:
    """OpenClaw acts as an MCP server for other CLIs; it must never be given to itself."""
    manifest_file = renderer.vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest_text = manifest_file.read_text(encoding="utf-8")
    manifest_file.write_text(
        manifest_text + "\n  openclaw:\n    tier: core\n    transport: stdio\n    command: openclaw\n    args: ['mcp', 'serve']\n    targets: [claude, codex, antigravity, opencode, openclaw]\n",
        encoding="utf-8",
    )
    cfg = openclaw_config(renderer.home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({
        "mcp": {"servers": {"openclaw": {"command": "openclaw", "args": ["mcp", "serve"]}}},
    }), encoding="utf-8")

    assert renderer.render_openclaw(write=True)[0]

    servers = _servers(renderer)
    assert "openclaw" not in servers


def test_openclaw_server_rendered_for_windows_uses_node_launcher(tmp_path: Path) -> None:
    """On Windows, openclaw mcp serve is launched via node script instead of openclaw.cmd."""
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir(parents=True)
    shim = npm_dir / "openclaw.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    entry = npm_dir / "node_modules" / "openclaw" / "openclaw.mjs"
    entry.parent.mkdir(parents=True)
    entry.write_text("// entry\n", encoding="utf-8")
    node_exe = npm_dir / "node.exe"
    node_exe.write_text("", encoding="utf-8")

    def fake_which(cmd: str) -> str | None:
        if cmd == "openclaw":
            return str(shim)
        if cmd == "node":
            return str(node_exe)
        return None

    vault = tmp_path / "vault"
    manifest_file = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest_file.parent.mkdir(parents=True)
    manifest_file.write_text(
        "schema_version: 1\nservers:\n  openclaw:\n    exposure: eager\n    transport: stdio\n    command: openclaw\n    args: ['mcp', 'serve']\n    targets: [claude, codex, antigravity, opencode]\n    enabled: true\n",
        encoding="utf-8",
    )
    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=tmp_path / "home", which=fake_which, is_windows=True)
    resolved = renderer.load_resolved_servers("claude")
    assert "openclaw" in resolved
    srv = resolved["openclaw"]
    assert srv["command"] == str(node_exe)
    assert srv["args"][0] == str(entry)
    assert srv["args"][1:] == ["mcp", "serve"]
    assert "openclaw.cmd" not in srv["command"]


def test_openclaw_server_rendered_without_env_secrets_or_references(tmp_path: Path) -> None:
    """Canonical openclaw mcp serve authenticates via local state without env block or cleartext tokens."""
    from nexgen_core.paths import mcp_manifest
    from nexgen_core.config import load_mcp_manifest
    from nexgen_core.mcp_state import enable_server

    # 1. Real engine manifest has no env block on openclaw
    engine_root = Path(__file__).resolve().parents[3]
    manifest_path = mcp_manifest(engine_root)
    assert manifest_path.is_file()
    data = load_mcp_manifest(manifest_path)
    srv_def = data["servers"]["openclaw"]
    assert "env" not in srv_def, "openclaw mcp serve authenticates via local openclaw.json; no env block needed"

    # 2. Rendered entries for all target CLIs carry no env block, tokens, or ${VAR} references
    home = tmp_path / "home"
    enable_server("openclaw", enabled=True, home=home)
    renderer = McpRenderer(vault_data=engine_root, home=home)
    for cli in ("claude", "codex", "antigravity", "opencode"):
        resolved = renderer.load_resolved_servers(cli)
        assert "openclaw" in resolved
        entry = resolved["openclaw"]
        assert "env" not in entry or not entry["env"], "no env block rendered for openclaw"
        dumped = json.dumps(entry)
        assert "OPENCLAW_GATEWAY" not in dumped
        assert "${" not in dumped


