"""The lazy gateway OpenClaw mounts for its CLI agents: one entry, references only.

A `claude-cli` agent never reads `~/.claude.json` (OpenClaw starts Claude Code with
`--strict-mcp-config --mcp-config <its own file>`), and its connectors are denied on
OpenClaw's `alwaysLoad` loopback server, so the engine's `lazy-mcp` entry in
`mcp.servers` is its only road to them. These tests pin what that entry carries: the
command the manifest declares, the CLI it serves, the runtime variables its children
need, and every variable the served connectors declare as a `${NAME}` reference,
never as a value and never as one of the paths the gateway resolves itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_render.openclaw import is_engine_lazy_mcp  # noqa: E402
from nexgen_core.paths import openclaw_config  # noqa: E402
from nexgen_core.renderer import IS_WINDOWS, McpRenderer  # noqa: E402

#: The renderer writes `python` on Windows, where no `python3` shim exists.
INTERPRETER = "python" if IS_WINDOWS else "python3"

MANIFEST = """schema_version: 1
servers:
  lazy-mcp:
    exposure: eager
    tier: core
    transport: stdio
    command: python3
    args: ["${AGENT_ENGINE_ROOT}/agent-universal-layer/mcp/lazy-mcp.py"]
    env:
      AGENT_VAULT_DATA: "${AGENT_VAULT_DATA}"
    targets: [claude, codex, antigravity, opencode, openclaw]
  vault-library:
    exposure: eager
    transport: http
    url: "${VAULT_LIBRARY_URL}"
    url_env: VAULT_LIBRARY_URL
    auth: {type: bearer, env: VAULT_LIBRARY_TOKEN}
    targets: [openclaw]
  tool:
    exposure: eager
    transport: stdio
    command: node
    args: ["tool.mjs", "${TOOL_HOME}/run"]
    env:
      TOOL_TOKEN: "${TOOL_TOKEN}"
    require_env: TOOL_HOME
    targets: [openclaw]
"""


@pytest.fixture
def renderer(tmp_path: Path, monkeypatch) -> McpRenderer:
    """A renderer over a synthetic vault, with every value these tests call secret."""
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    monkeypatch.setenv("VAULT_LIBRARY_URL", "http://127.0.0.1:5678/mcp-deadbeef")
    monkeypatch.setenv("VAULT_LIBRARY_TOKEN", "vault-token-never-written")
    monkeypatch.setenv("TOOL_HOME", "/opt/tool")
    monkeypatch.setenv("TOOL_TOKEN", "tool-token-never-written")
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(MANIFEST, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


def _install(renderer: McpRenderer, servers: dict | None = None) -> Path:
    path = openclaw_config(renderer.home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcp": {"servers": servers or {}}}), encoding="utf-8")
    return path


def _servers(renderer: McpRenderer) -> dict:
    return json.loads(openclaw_config(renderer.home).read_text(encoding="utf-8"))["mcp"]["servers"]


def test_openclaw_gets_the_gateway_with_every_served_connectors_variables(renderer) -> None:
    """The CLI agent's only road to a connector is this entry: it must be able to spawn it."""
    _install(renderer)
    assert renderer.render_openclaw(write=True)[0]

    gateway = _servers(renderer)["lazy-mcp"]
    assert is_engine_lazy_mcp(gateway) is True
    assert gateway["command"] == INTERPRETER
    assert any("lazy-mcp.py" in argument for argument in gateway["args"])

    env = gateway["env"]
    assert env["LAZY_MCP_CLI"] == "openclaw"
    # PATH and HOME come from the SDK's default environment: a reference would
    # freeze their setup-time value in the gateway's `.env`.
    assert "PATH" not in env and "HOME" not in env
    # Every declaration of every served connector arrives as a reference.
    assert env["VAULT_LIBRARY_URL"] == "${VAULT_LIBRARY_URL}"
    assert env["VAULT_LIBRARY_TOKEN"] == "${VAULT_LIBRARY_TOKEN}"
    assert env["TOOL_HOME"] == "${TOOL_HOME}"
    assert env["TOOL_TOKEN"] == "${TOOL_TOKEN}"


def test_the_gateway_entry_carries_no_engine_path_and_no_literal_secret(renderer) -> None:
    """The gateway resolves its own paths, and a secret never leaves the environment."""
    path = _install(renderer)
    assert renderer.render_openclaw(write=True)[0]

    entry = _servers(renderer)["lazy-mcp"]
    for resolved in ("AGENT_ENGINE_ROOT", "AGENT_VAULT_DATA", "KNOWLEDGE_VAULT_PATH"):
        assert resolved not in entry["env"]
    text = path.read_text(encoding="utf-8")
    assert "vault-token-never-written" not in text
    assert "tool-token-never-written" not in text
    assert "http://127.0.0.1:5678/mcp-deadbeef" not in text


def test_a_hand_written_gateway_entry_is_replaced_by_the_engines_own(renderer) -> None:
    """The manifest owns the name: a leftover custom proxy cannot keep the agents disconnected."""
    _install(renderer, {"lazy-mcp": {"command": "node", "args": ["custom-proxy.js"]}})
    assert renderer.render_openclaw(write=True)[0]

    gateway = _servers(renderer)["lazy-mcp"]
    assert gateway["command"] == INTERPRETER
    assert gateway["env"]["LAZY_MCP_CLI"] == "openclaw"
    assert gateway["env"]["TOOL_TOKEN"] == "${TOOL_TOKEN}"
