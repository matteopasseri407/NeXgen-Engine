"""OpenCode 2 MCP rendering and inventory use the native nested schema."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import renderer_cli  # noqa: E402
from nexgen_core.checks.mcp_checks import _rendered_names_opencode  # noqa: E402
from nexgen_core.jsonc import parse_jsonc  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402


def _renderer(tmp_path: Path) -> McpRenderer:
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("""schema_version: 1
retired_servers: [retired]
servers:
  managed:
    tier: core
    transport: stdio
    command: python3
    args: ["-m", "managed"]
    env: {DEMO: value}
    timeouts: {startup: 45, tool: 60}
    tools_deny: [expensive_tool]
  remote:
    tier: core
    transport: http
    url: https://example.invalid/mcp
    oauth: true
    oauth_client_id: demo-client
  public:
    tier: core
    transport: http
    url: https://public.example.invalid/mcp
  keyed:
    tier: core
    transport: http
    url: https://keyed.example.invalid/mcp
    auth: {type: bearer, env: DEMO_TOKEN}
""", encoding="utf-8")
    return McpRenderer(vault_data=vault, home=tmp_path / "home")


def test_opencode_v2_migrates_legacy_and_preserves_unmanaged_servers(tmp_path: Path, monkeypatch) -> None:
    renderer = _renderer(tmp_path)
    cfg = renderer.opencode_config_path()
    cfg.parent.mkdir(parents=True)
    cfg.write_text("""{
  // user's setting survives
  "tools": {"websearch": true, "managed_expensive_tool": false},
  "mcp": {
    "managed": {"type": "local", "command": ["old"], "enabled": true},
    "retired": {"type": "local", "command": ["retired"]},
    "outside": {"type": "remote", "url": "https://other.invalid/mcp", "enabled": false,
                "oauth": {"clientId": "outside-client"}, "timeout": 9000}
  }
} """, encoding="utf-8")

    assert renderer.render_opencode(write=True)[0]
    raw = cfg.read_text(encoding="utf-8")
    data = parse_jsonc(raw)
    assert "// user's setting survives" in raw
    assert set(data["mcp"]) == {"servers"}
    servers = data["mcp"]["servers"]
    assert set(servers) == {"managed", "remote", "public", "keyed", "outside"}
    assert servers["managed"] == {
        "type": "local", "command": ["python" if sys.platform == "win32" else "python3", "-m", "managed"],
        "environment": {"DEMO": "value"},
        "timeout": {"startup": 45000, "execution": 60000},
    }
    assert servers["remote"] == {
        "type": "remote", "url": "https://example.invalid/mcp",
        "oauth": {"client_id": "demo-client"},
    }
    assert servers["public"] == {"type": "remote", "url": "https://public.example.invalid/mcp"}
    assert servers["keyed"] == {
        "type": "remote", "url": "https://keyed.example.invalid/mcp",
        "headers": {"Authorization": "Bearer {env:DEMO_TOKEN}"}, "oauth": False,
    }
    assert servers["outside"]["disabled"] is True
    assert servers["outside"]["oauth"] == {"client_id": "outside-client"}
    assert servers["outside"]["timeout"] == {"execution": 9000}
    assert data["tools"] == {"websearch": True}
    assert {"action": "managed_expensive_tool", "resource": "*", "effect": "deny"} in data["permissions"]
    assert _rendered_names_opencode(cfg) == set(servers)

    monkeypatch.setattr(renderer_cli, "_cli_config_path", lambda cli: cfg)
    assert set(renderer_cli._load_live("opencode")) == set(servers)
    renderer.render_opencode(write=True)
    assert cfg.read_text(encoding="utf-8") == raw


def test_opencode_v2_preserves_native_options_and_other_cli_output(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    cfg = renderer.opencode_config_path()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({
        "mcp": {"timeout": {"catalog": 34000}, "servers": {
            "outside": {"type": "local", "command": ["outside"], "codemode": False},
        }},
        "permissions": [{"action": "bash", "resource": "*", "effect": "ask"}],
        "tools": {"websearch": False},
    }), encoding="utf-8")
    renderer.render_opencode(write=True)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcp"]["timeout"] == {"catalog": 34000}
    assert data["mcp"]["servers"]["outside"]["codemode"] is False
    assert data["permissions"][0] == {"action": "bash", "resource": "*", "effect": "ask"}
    assert data["tools"] == {"websearch": False}

    renderer.render_claude(write=True)
    renderer.render_codex(write=True)
    renderer.render_antigravity(write=True)
    claude = json.loads((renderer.home / ".claude.json").read_text())
    antigravity = json.loads((renderer.home / ".gemini/antigravity/mcp_config.json").read_text())
    codex = (renderer.home / ".codex/config.toml").read_text()
    assert "managed" in claude["mcpServers"]
    assert "managed" in antigravity["mcpServers"]
    assert "[mcp_servers.managed]" in codex


def test_opencode_v2_binary_accepts_rendered_schema(tmp_path: Path) -> None:
    binary = shutil.which("opencode")
    if not binary:
        pytest.skip("OpenCode CLI is not installed")
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True).stdout
    if "v2." not in version:
        pytest.skip("OpenCode 2 is not installed")

    renderer = _renderer(tmp_path)
    renderer.render_opencode(write=True)
    cfg = renderer.opencode_config_path()
    (tmp_path / "opencode.jsonc").write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")
    env = dict(os.environ)
    env.pop("OPENCODE_CONFIG", None)
    env.pop("OPENCODE_CONFIG_CONTENT", None)
    env.pop("OPENCODE_DISABLE_PROJECT_CONFIG", None)
    result = subprocess.run([binary, "debug", "config"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    sources = json.loads(result.stdout)
    server_maps = [source.get("info", {}).get("mcp", {}).get("servers", {}) for source in sources]
    rendered = next((servers for servers in server_maps if "managed" in servers), {})
    assert {"managed", "remote", "public", "keyed"} <= set(rendered), [sorted(s) for s in server_maps]
    assert rendered["managed"]["timeout"]["execution"] == 60000


def test_cli_paths_point_at_canonical_files_not_fanouts(tmp_path: Path, monkeypatch) -> None:
    """Single-source config paths: revert/reset must operate where the
    renderer writes and the backups live. Antigravity's canonical file is
    `antigravity/mcp_config.json`; the `-ide` copy is a fan-out symlink,
    and resetting the symlink would orphan the canonical file."""
    import sys as _sys

    from nexgen_core import renderer_cli  # noqa: E402

    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(renderer_cli, "HOME", home)

    assert renderer_cli._cli_config_path("antigravity") == home / ".gemini" / "antigravity" / "mcp_config.json"
    assert renderer_cli._cli_config_path("claude") == home / ".claude.json"
    assert renderer_cli._cli_config_path("codex") == home / ".codex" / "config.toml"
    candidates = renderer_cli._cli_config_candidates("antigravity")
    assert candidates[0].parent.name == "antigravity"
    assert any(p.parent.name == "antigravity-ide" for p in candidates)
    assert _sys.platform != "win32" or renderer_cli._cli_config_path("opencode").is_absolute()
