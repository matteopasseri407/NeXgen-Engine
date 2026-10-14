"""Rendering must preserve private connectors and the last usable config."""
from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import yaml

from nexgen_core.renderer import McpRenderer
from nexgen_core import renderer_cli
from nexgen_core import files


def renderer_fixture(tmp_path, name="managed"):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump({"servers": {
        name: {"transport": "stdio", "command": "python", "args": ["-V"], "tier": "core"}
    }}))
    config = home / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home), config


def test_codex_dotted_connector_name_is_one_key_and_rerenders(tmp_path):
    renderer, config = renderer_fixture(tmp_path, "local.plugin")
    renderer.render_codex(write=True)
    parsed = tomllib.loads(config.read_text())
    assert parsed["mcp_servers"]["local.plugin"]["command"] == "python"
    before = config.read_bytes()
    renderer.render_codex(write=True)
    assert config.read_bytes() == before


def test_codex_unreadable_existing_config_is_never_replaced(tmp_path, monkeypatch):
    renderer, config = renderer_fixture(tmp_path)
    config.write_text('model = "private-default"\n')
    before = config.read_bytes()
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == config:
            raise PermissionError("synthetic unreadable config")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises((OSError, ValueError)):
        renderer.render_codex(write=True)
    assert config.read_bytes() == before
    assert sorted(p.name for p in config.parent.iterdir()) == ["config.toml"]


def test_codex_invalid_existing_toml_stops_before_write(tmp_path):
    renderer, config = renderer_fixture(tmp_path)
    config.write_text('model = "unfinished\n')
    before = config.read_bytes()
    with pytest.raises(ValueError):
        renderer.render_codex(write=True)
    assert config.read_bytes() == before
    assert sorted(p.name for p in config.parent.iterdir()) == ["config.toml"]


def test_codex_unknown_local_connector_and_settings_survive(tmp_path):
    renderer, config = renderer_fixture(tmp_path)
    config.write_text('model = "private-default"\n[mcp_servers."private.local"]\n'
                      'command = "node"\nargs = ["private-plugin.js"]\n'
                      '[mcp_servers."private.local".env]\nLOCAL_MODE = "manual"\n')
    before = tomllib.loads(config.read_text())
    renderer.render_codex(write=True)
    after = tomllib.loads(config.read_text())
    assert after["model"] == before["model"]
    assert after["mcp_servers"]["private.local"] == before["mcp_servers"]["private.local"]


def test_revert_backups_never_overwrite_each_other_at_same_second(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text("version = 1\n")
    monkeypatch.setattr(files.time, "strftime", lambda *_: "fixed-clock")
    first = renderer_cli._secure_backup(config, "version = 1\n")
    second = renderer_cli._secure_backup(config, "version = 2\n")
    assert first != second
    assert first.read_text() == "version = 1\n"
    assert second.read_text() == "version = 2\n"


@pytest.mark.parametrize("allowed_here", [True, False])
def test_env_gated_connector_is_preserved_only_for_its_declared_runtime(tmp_path, monkeypatch, allowed_here):
    renderer, config = renderer_fixture(tmp_path)
    monkeypatch.delenv("PRIVATE_CONNECTOR_ENABLED", raising=False)
    manifest = {"servers": {"local-private": {
        "transport": "stdio", "command": "node", "args": ["private-plugin.js"],
        "tier": "core", "require_env": "PRIVATE_CONNECTOR_ENABLED",
        "targets": ["codex" if allowed_here else "claude"],
    }}}
    renderer.manifest_path.write_text(yaml.safe_dump(manifest))
    config.write_text('[mcp_servers.local_private]\ncommand = "node"\nargs = ["private-plugin.js"]\n')
    renderer.render_codex(write=True)
    assert ("local_private" in tomllib.loads(config.read_text()).get("mcp_servers", {})) is allowed_here
    live = {"local-private": {"command": "node"}}
    renderer._drop_unmounted(live, {}, cli_target="codex")
    assert ("local-private" in live) is allowed_here


def test_codex_order_does_not_depend_on_the_environment(tmp_path, monkeypatch):
    """The guard runs without the shell's env: an env-gated server it cannot
    resolve keeps its manifest slot, so guard and interactive apply agree."""
    home, vault = tmp_path / "home", tmp_path / "vault"
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump({"servers": {
        "alpha": {"transport": "stdio", "command": "a", "args": [], "tier": "core"},
        "gated": {"transport": "stdio", "command": "g", "args": [], "tier": "core", "require_env": "NEXGEN_TEST_GATE"},
        "omega": {"transport": "stdio", "command": "o", "args": [], "tier": "core"},
    }}, sort_keys=False))
    config = home / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model = "private"\n\n[mcp_servers.private_tool]\ncommand = "p"\n')
    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)

    monkeypatch.setenv("NEXGEN_TEST_GATE", "1")
    renderer.render_codex(write=True)
    interactive = config.read_text()
    monkeypatch.delenv("NEXGEN_TEST_GATE")
    renderer.render_codex(write=True)

    assert config.read_text() == interactive
    sections = [line for line in interactive.splitlines() if line.startswith("[mcp_servers.")]
    assert sections == ["[mcp_servers.private_tool]", "[mcp_servers.alpha]", "[mcp_servers.gated]", "[mcp_servers.omega]"]


def test_mcp_checks_give_the_same_verdict_with_and_without_the_gate(tmp_path, monkeypatch):
    """Guard (no shell env) and shell must not disagree on orphans."""
    from nexgen_core.checks.mcp_checks import check_mcp_orphans
    from nexgen_core.report import Severity

    home, vault = tmp_path / "home", tmp_path / "vault"
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump({"servers": {
        "gated": {"transport": "stdio", "command": "g", "args": [], "tier": "core", "require_env": "NEXGEN_TEST_GATE"},
    }}))
    (home / ".codex").mkdir(parents=True)
    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    monkeypatch.setenv("NEXGEN_TEST_GATE", "1")
    renderer.render_codex(write=True)

    with_gate = check_mcp_orphans(vault, home).severity
    monkeypatch.delenv("NEXGEN_TEST_GATE")
    without_gate = check_mcp_orphans(vault, home).severity
    assert with_gate == without_gate == Severity.OK


def test_a_hand_written_hyphen_section_is_the_manifest_server(tmp_path):
    """`[mcp_servers.my-server]` on disk is the manifest's `my-server`: one
    section afterwards, in Codex's spelling, not a foreign duplicate."""
    renderer, config = renderer_fixture(tmp_path, "my-server")
    config.write_text('[mcp_servers.my-server]\ncommand = "old"\n')
    renderer.render_codex(write=True)
    servers = tomllib.loads(config.read_text())["mcp_servers"]
    assert list(servers) == ["my_server"]
    assert servers["my_server"]["command"] == "python"
