"""Claude Desktop app dialect: default-deny allowlist, additive preferences, isolation."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_render.claude_desktop import allowed_desktop_servers  # noqa: E402
from nexgen_core.paths import claude_desktop_config  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_desktop_home(monkeypatch):
    # claude_desktop_config honors XDG_CONFIG_HOME (POSIX) and APPDATA
    # (Windows): without this, a host carrying either variable renders
    # into the real app config instead of the tmp home.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)


def make_renderer(tmp_path, servers=None):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(yaml.safe_dump({"servers": servers or {
        "alpha": {"transport": "stdio", "command": "python", "args": ["-V"], "tier": "core"},
    }}))
    home.mkdir(parents=True, exist_ok=True)
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


def desktop_file(tmp_path) -> Path:
    return claude_desktop_config(tmp_path / "home")


def test_paths_stay_inside_the_given_home(tmp_path, monkeypatch):
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
        "alpha": {"transport": "stdio", "command": "/usr/bin/python3", "args": ["-V"],
                  "env": {"MODE": "x"}, "tier": "core", "desktop_allow": True},
        "mutating": {"transport": "stdio", "command": "/usr/bin/python3", "args": ["-V"],
                     "tier": "core"},
    })
    desktop_file(tmp_path).parent.mkdir(parents=True)
    ok, _ = renderer.render_claude_desktop(write=True)
    assert ok
    table = json.loads(desktop_file(tmp_path).read_text(encoding="utf-8"))["mcpServers"]
    assert table["alpha"] == {"command": "/usr/bin/python3", "args": ["-V"], "env": {"MODE": "x"}}
    assert "type" not in table["alpha"]
    assert "mutating" not in table


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
    manifest.parent.mkdir(parents=True, exist_ok=True)
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


def test_trimmed_allowlisted_server_keeps_its_tool_filter(tmp_path):
    renderer = make_renderer(tmp_path, {
        "filtered": {"transport": "stdio", "command": "python3", "args": ["srv.py"], "tier": "core",
                     "tools_deny": ["dangerous_tool"], "desktop_allow": True},
    })
    desktop_file(tmp_path).parent.mkdir(parents=True)
    renderer.render_claude_desktop(write=True)
    entry = json.loads(desktop_file(tmp_path).read_text(encoding="utf-8"))["mcpServers"]["filtered"]
    assert entry["args"][:2] != ["srv.py"]
    assert entry["command"] or entry["args"]
    assert "mcp-trim.py" in " ".join([entry["command"], *entry["args"]])
    assert entry["env"]["LAZY_MCP_CLI"] == "claude-desktop"


def test_gateway_flagged_is_never_rendered(tmp_path):
    renderer = make_renderer(tmp_path, {
        "lazy-mcp": {"transport": "stdio", "command": "python3", "args": ["lazy.py"],
                     "exposure": "eager", "desktop_allow": True},
    })
    assert allowed_desktop_servers(renderer) == {}


def test_revoking_the_flag_removes_only_what_the_renderer_wrote(tmp_path):
    def servers(allow):
        entry = {"transport": "stdio", "command": "python", "tier": "core"}
        if allow:
            entry["desktop_allow"] = True
        return {"alpha": entry}

    renderer = make_renderer(tmp_path, servers(True))
    cfg = desktop_file(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"handmade": {"command": "x"}}}), encoding="utf-8")
    renderer.render_claude_desktop(write=True)
    assert set(json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]) == {"alpha", "handmade"}

    renderer2 = make_renderer(tmp_path, servers(False))
    renderer2.render_claude_desktop(write=True)
    live = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]
    assert set(live) == {"handmade"}


def test_unreadable_desktop_config_without_expected_servers_is_skipped(tmp_path):
    from nexgen_core.checks import mcp_checks
    from nexgen_core.report import Severity

    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(yaml.safe_dump({"servers": {
        "alpha": {"transport": "stdio", "command": "echo", "tier": "core",
                  "targets": ["claude", "codex", "antigravity", "opencode"]},
    }}))
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"alpha": {"command": "echo"}}}))
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    (home / ".gemini" / "antigravity" / "mcp_config.json").write_text(
        json.dumps({"mcpServers": {"alpha": {"command": "echo"}}}))
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('[mcp_servers.alpha]\ncommand = "echo"\n')
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".config" / "opencode" / "opencode.json").write_text(
        json.dumps({"mcp": {"servers": {"alpha": {"command": "echo"}}}}))
    cfg = desktop_file(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{not json", encoding="utf-8")

    outcome = mcp_checks.check_mcp_configs_rendered(vault, home)
    assert outcome.severity == Severity.OK


def test_no_app_directory_means_no_file_created(tmp_path):
    renderer = make_renderer(tmp_path, {
        "alpha": {"transport": "stdio", "command": "python", "tier": "core", "desktop_allow": True},
    })
    assert not (tmp_path / "home" / ".config" / "Claude").exists()
    ok, _ = renderer.render_claude_desktop(write=True)
    assert ok
    assert not desktop_file(tmp_path).is_file()


def test_bare_commands_resolve_to_absolute_paths(tmp_path):
    import os

    renderer = make_renderer(tmp_path, {
        "alpha": {"transport": "stdio", "command": "python3", "tier": "core", "desktop_allow": True},
        "ghost": {"transport": "stdio", "command": "definitely-not-a-real-binary-xyz",
                  "tier": "core", "desktop_allow": True},
    })
    cfg = desktop_file(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({}), encoding="utf-8")
    renderer.render_claude_desktop(write=True)
    table = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]
    if os.environ.get("PATH"):
        assert os.path.isabs(table["alpha"]["command"])
    assert table["ghost"]["command"] == "definitely-not-a-real-binary-xyz"


def test_windows_paths_honor_appdata_and_fall_back_under_home(tmp_path, monkeypatch):
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert str(claude_desktop_config(tmp_path / "home")).startswith(str(tmp_path / "appdata"))
    monkeypatch.delenv("APPDATA")
    fallback = claude_desktop_config(tmp_path / "home")
    assert str(fallback).startswith(str(tmp_path / "home"))
    assert fallback.name == "claude_desktop_config.json"


def test_guard_without_the_shell_env_keeps_env_gated_desktop_entries(tmp_path, monkeypatch):
    """The guard runs without OCR_TUNNEL_PORT: what the shell wrote must survive it."""
    renderer = make_renderer(tmp_path, {
        "ocr": {"transport": "stdio", "command": "/usr/bin/python3", "args": ["ocr.py"], "tier": "core",
                "require_env": "OCR_TUNNEL_PORT", "desktop_allow": True},
    })
    desktop_file(tmp_path).parent.mkdir(parents=True)
    monkeypatch.setenv("OCR_TUNNEL_PORT", "9000")
    renderer.render_claude_desktop(write=True)
    interactive = desktop_file(tmp_path).read_text(encoding="utf-8")

    monkeypatch.delenv("OCR_TUNNEL_PORT")
    renderer.render_claude_desktop(write=True)
    assert desktop_file(tmp_path).read_text(encoding="utf-8") == interactive
    assert renderer.pending_changes("claude-desktop") == {}


def test_a_command_resolved_earlier_survives_a_different_path(tmp_path, monkeypatch):
    """Guard and shell see different PATHs: the absolute command must not flip."""
    bins = []
    for folder in ("shell-bin", "guard-bin"):
        # Windows resolves a bare name only through PATHEXT.
        exe = tmp_path / folder / ("tool.exe" if os.name == "nt" else "tool")
        exe.parent.mkdir()
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        bins.append(exe)
    renderer = make_renderer(tmp_path, {
        "alpha": {"transport": "stdio", "command": "tool", "args": [], "tier": "core", "desktop_allow": True},
    })
    desktop_file(tmp_path).parent.mkdir(parents=True)
    monkeypatch.setenv("PATH", str(bins[0].parent))
    renderer.render_claude_desktop(write=True)
    monkeypatch.setenv("PATH", str(bins[1].parent))
    renderer.render_claude_desktop(write=True)
    table = json.loads(desktop_file(tmp_path).read_text(encoding="utf-8"))["mcpServers"]
    # Windows answers with PATHEXT casing (tool.EXE): compare as the OS does.
    assert os.path.normcase(table["alpha"]["command"]) == os.path.normcase(str(bins[0]))


@pytest.mark.parametrize(("name", "windows", "expected"), [
    ("tool", False, True),
    ("tool.exe", False, False),
    ("tool.EXE", True, True),
    ("TOOL.cmd", True, True),
    ("tool.txt", True, False),
    ("other.exe", True, False),
])
def test_a_previous_command_is_recognised_the_way_which_found_it(monkeypatch, name, windows, expected):
    from nexgen_core.mcp_render import claude_desktop

    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    assert claude_desktop._names_command(f"/x/{name}", "tool", windows=windows) is expected
