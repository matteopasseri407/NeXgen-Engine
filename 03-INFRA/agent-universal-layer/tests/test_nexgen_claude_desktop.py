"""Claude Desktop app dialect: default-deny allowlist, additive preferences, isolation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_render.claude_desktop import allowed_desktop_servers  # noqa: E402
from nexgen_core.paths import claude_desktop_config  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402


def make_renderer(tmp_path, servers=None):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump({"servers": servers or {
        "alpha": {"transport": "stdio", "command": "python", "args": ["-V"], "tier": "core"},
    }}))
    home.mkdir()
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


def desktop_file(tmp_path) -> Path:
    return claude_desktop_config(tmp_path / "home")


def test_paths_stay_inside_the_given_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    path = claude_desktop_config(tmp_path / "home")
    assert path.name == "claude_desktop_config.json"
    assert str(path).startswith(str(tmp_path / "home"))


def test_default_deny_leaves_a_live_file_byte_identical(tmp_path):
    renderer = make_renderer(tmp_path)
    cfg = desktop_file(tmp_path)
    cfg.parent.mkdir(parents=True)
    live = {"preferences": {"sidebarMode": "epitaxy"}, "coworkUserFilesPath": "UNTOUCHED"}
    cfg.write_text(json.dumps(live, indent=2), encoding="utf-8")
    before = cfg.read_bytes()
    ok, _ = renderer.render_claude_desktop(write=True)
    assert ok
    assert cfg.read_bytes() == before
    assert "mcpServers" not in json.loads(cfg.read_text(encoding="utf-8"))
    assert allowed_desktop_servers(renderer) == {}


def test_allowlisted_stdio_renders_in_the_classic_desktop_shape(tmp_path):
    renderer = make_renderer(tmp_path, {
        "alpha": {"transport": "stdio", "command": "python", "args": ["-V"],
                  "env": {"MODE": "x"}, "tier": "core", "desktop_allow": True},
        "mutating": {"transport": "stdio", "command": "python", "args": ["-V"],
                     "tier": "core"},
    })
    ok, _ = renderer.render_claude_desktop(write=True)
    assert ok
    entry = json.loads(desktop_file(tmp_path).read_text(encoding="utf-8"))["mcpServers"]["alpha"]
    assert entry == {"command": "python", "args": ["-V"], "env": {"MODE": "x"}}
    assert "type" not in entry
    assert "mutating" not in json.loads(desktop_file(tmp_path).read_text(encoding="utf-8"))["mcpServers"]


def test_env_gated_allowlisted_server_is_omitted_without_its_env(tmp_path, monkeypatch):
    monkeypatch.delenv("OCR_TUNNEL_PORT", raising=False)
    renderer = make_renderer(tmp_path, {
        "ocr": {"transport": "stdio", "command": "python3", "args": ["ocr.py"], "tier": "core",
                "require_env": "OCR_TUNNEL_PORT", "desktop_allow": True},
    })
    assert allowed_desktop_servers(renderer) == {}
    ok, _ = renderer.render_claude_desktop(write=True)
    assert ok
    assert not desktop_file(tmp_path).is_file()


def test_retired_allowlisted_server_is_removed_but_preferences_survive(tmp_path):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump({
        "retired_servers": ["gone"],
        "servers": {"gone": {"transport": "stdio", "command": "python", "tier": "core",
                             "desktop_allow": True}},
    }))
    home.mkdir()
    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    cfg = desktop_file(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"preferences": {"a": 1},
                               "mcpServers": {"gone": {"command": "python"}}}), encoding="utf-8")
    renderer.render_claude_desktop(write=True)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"] == {}
    assert data["preferences"] == {"a": 1}


def test_non_object_mcp_servers_stops_the_render(tmp_path):
    import pytest

    renderer = make_renderer(tmp_path, {
        "alpha": {"transport": "stdio", "command": "python", "tier": "core", "desktop_allow": True},
    })
    cfg = desktop_file(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": ["not", "an", "object"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="mcpServers must be an object"):
        renderer.render_claude_desktop(write=True)


def test_hand_added_manifest_server_is_kept_and_flagged(tmp_path):
    from nexgen_core.checks.mcp_checks import check_desktop_allowlist
    from nexgen_core.report import Severity

    renderer = make_renderer(tmp_path, {
        "alpha": {"transport": "stdio", "command": "python", "tier": "core", "desktop_allow": True},
        "n8n": {"transport": "stdio", "command": "python", "tier": "core"},
    })
    cfg = desktop_file(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"n8n": {"command": "python"}}}), encoding="utf-8")
    renderer.render_claude_desktop(write=True)
    live = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]
    assert set(live) == {"alpha", "n8n"}
    outcome = check_desktop_allowlist(tmp_path / "vault", tmp_path / "home")
    assert outcome.severity == Severity.WARN
    assert "n8n" in outcome.message


def test_allowlist_check_is_quiet_when_clean_or_absent(tmp_path):
    from nexgen_core.checks.mcp_checks import check_desktop_allowlist
    from nexgen_core.report import Severity

    vault, home = tmp_path / "vault", tmp_path / "home"
    assert check_desktop_allowlist(vault, home).severity == Severity.OK
    make_renderer(tmp_path)
    cfg = desktop_file(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"some-ui-tool": {"command": "x"}}}), encoding="utf-8")
    outcome = check_desktop_allowlist(tmp_path / "vault", tmp_path / "home")
    assert outcome.severity == Severity.OK


def test_render_all_covers_desktop_and_isolates_its_corruption(tmp_path):
    renderer = make_renderer(tmp_path, {
        "alpha": {"transport": "stdio", "command": "python", "tier": "core", "desktop_allow": True},
    })
    cfg = desktop_file(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{not json", encoding="utf-8")
    results = renderer.render_all(write=True)
    assert results["claude-desktop"] is False
    assert results["claude"] is True
