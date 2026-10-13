"""Install-time absorb offer: the Claude Desktop app's MCP servers surface in `nexgen init`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import renderer_cli  # noqa: E402
from nexgen_core.bootstrap import desktop_app_orphans, print_desktop_stubs  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def _home_with_desktop(tmp_path: Path, servers: dict | None) -> Path:
    home = tmp_path / "home"
    cfg_dir = home / ".config" / "Claude"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    if servers is not None:
        (cfg_dir / "claude_desktop_config.json").write_text(
            json.dumps({"preferences": {}, "mcpServers": servers}), encoding="utf-8")
    return home


def _vault_with_manifest(tmp_path: Path, servers: dict | None) -> Path:
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if servers is not None:
        manifest.write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")
    return vault


def test_no_app_config_means_no_orphans(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    vault = _vault_with_manifest(tmp_path, {"a": {"command": "x"}})
    assert desktop_app_orphans(vault, home) == []


def test_malformed_app_config_means_no_orphans(tmp_path):
    home = _home_with_desktop(tmp_path, None)
    (home / ".config" / "Claude" / "claude_desktop_config.json").write_text("{nope", encoding="utf-8")
    vault = _vault_with_manifest(tmp_path, {"a": {"command": "x"}})
    assert desktop_app_orphans(vault, home) == []


@pytest.mark.parametrize("body", ["[]", '"just a string"', "null", "42"])
def test_non_object_app_config_means_no_orphans_not_a_crash(tmp_path, body):
    home = _home_with_desktop(tmp_path, None)
    (home / ".config" / "Claude" / "claude_desktop_config.json").write_text(body, encoding="utf-8")
    vault = _vault_with_manifest(tmp_path, {"a": {"command": "x"}})
    assert desktop_app_orphans(vault, home) == []


def test_live_minus_manifest_is_offered_sorted(tmp_path):
    home = _home_with_desktop(tmp_path, {
        "zebra": {"command": "x"}, "alpha": {"command": "y"}, "known": {"command": "z"}})
    vault = _vault_with_manifest(tmp_path, {"known": {"command": "z"}})
    assert desktop_app_orphans(vault, home) == ["alpha", "zebra"]


def test_everything_absorbed_means_silence(tmp_path):
    home = _home_with_desktop(tmp_path, {"known": {"command": "z"}})
    vault = _vault_with_manifest(tmp_path, {"known": {"command": "z"}})
    assert desktop_app_orphans(vault, home) == []


def test_unreadable_manifest_counts_everything_live(tmp_path):
    home = _home_with_desktop(tmp_path, {"a": {"command": "x"}})
    vault = tmp_path / "vault"
    vault.mkdir()
    assert desktop_app_orphans(vault, home) == ["a"]


def test_stubs_print_reviewable_entries_and_nothing_else(tmp_path, monkeypatch, capsys):
    home = _home_with_desktop(tmp_path, {"ui-tool": {"command": "do-thing", "args": ["--x"]}})
    monkeypatch.setattr(renderer_cli, "HOME", home)
    print_desktop_stubs(["ui-tool"], home)
    out = capsys.readouterr().out
    assert "ui-tool" in out
    assert "do-thing" in out
    assert "desktop_allow" in out
    assert json.loads((home / ".config" / "Claude" / "claude_desktop_config.json").read_text())[
        "mcpServers"] == {"ui-tool": {"command": "do-thing", "args": ["--x"]}}


def test_rendered_managed_servers_are_not_offered_back(tmp_path, monkeypatch):
    from nexgen_core.bootstrap import desktop_app_orphans
    from nexgen_core.renderer import McpRenderer

    vault = _vault_with_manifest(tmp_path, {
        "alpha": {"transport": "stdio", "command": "/usr/bin/python3",
                  "tier": "core", "desktop_allow": True},
    })
    home = _home_with_desktop(tmp_path, {"handmade": {"command": "x"}})
    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    assert renderer.render_claude_desktop(write=True)[0]
    assert desktop_app_orphans(vault, home) == ["handmade"]


def test_one_bad_entry_does_not_eat_the_good_stubs(tmp_path, monkeypatch, capsys):
    home = _home_with_desktop(tmp_path, {
        "good": {"command": "do-thing"},
        "bad name!": {"command": "x"},
    })
    monkeypatch.setattr(renderer_cli, "HOME", home)
    print_desktop_stubs(["good", "bad name!"], home)
    out = capsys.readouterr().out
    assert "good" in out and "do-thing" in out
    assert "bad name!" in out


def test_secret_shaped_env_never_reaches_the_terminal(tmp_path, monkeypatch, capsys):
    home = _home_with_desktop(tmp_path, {
        "leaky": {"command": "do-thing", "env": {"API_KEY": "sk-live-0123456789abcdef", "MODE": "x"}},
    })
    monkeypatch.setattr(renderer_cli, "HOME", home)
    print_desktop_stubs(["leaky"], home)
    out = capsys.readouterr().out
    assert "sk-live-0123456789abcdef" not in out
    assert "${API_KEY}" in out
    assert '"MODE": "x"' in out


def test_args_and_url_credentials_are_scrubbed_too(tmp_path, monkeypatch, capsys):
    home = _home_with_desktop(tmp_path, {
        "db": {"command": "do-thing",
               "args": ["postgres://analyst:s3cret-pw@db/box", "--api-key", "sk-live-abcdef0123456789"]},
    })
    monkeypatch.setattr(renderer_cli, "HOME", home)
    print_desktop_stubs(["db"], home)
    out = capsys.readouterr().out
    assert "s3cret-pw" not in out
    assert "sk-live-abcdef0123456789" not in out
    assert "postgres://<hidden>@db/box" in out
    assert "<hidden>" in out


def test_corrupt_file_between_detect_and_stubs_prints_and_stops(tmp_path, monkeypatch, capsys):
    from nexgen_core.bootstrap import desktop_app_orphans

    home = _home_with_desktop(tmp_path, {"late": {"command": "x"}})
    assert desktop_app_orphans(tmp_path / "vault", home) == ["late"]
    (home / ".config" / "Claude" / "claude_desktop_config.json").write_text("{truncated", encoding="utf-8")
    print_desktop_stubs(["late"], home)
    assert "nothing to absorb" in capsys.readouterr().out


def test_non_required_finding_never_blocks_the_install():
    from nexgen_core import bootstrap

    absorb = bootstrap.Finding("Claude Desktop app holds 1 MCP server(s) outside the manifest: x",
                               False, required=False, remedy="absorb them")
    assert bootstrap.render([absorb], "Assistants") == 0
