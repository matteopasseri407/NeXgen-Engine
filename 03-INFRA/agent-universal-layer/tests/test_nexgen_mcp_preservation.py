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
