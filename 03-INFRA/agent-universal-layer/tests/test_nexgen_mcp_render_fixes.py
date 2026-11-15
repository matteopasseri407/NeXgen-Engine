"""The per-CLI configuration writers: what each one must not lose, rewrite or refuse."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import files  # noqa: E402
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


def backups(path: Path) -> list[Path]:
    return sorted(p for p in path.parent.iterdir() if p.name.startswith(path.name + ".") and p.name.endswith(".bak"))


def _symlinks_supported() -> bool:
    """Whether this machine may create a symlink at all: Windows denies it without a privilege."""
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "target"
        target.write_text("x", encoding="utf-8")
        try:
            (Path(folder) / "link").symlink_to(target)
        except OSError:
            return False
        return True


#: Probed once, at import: a machine that may not create a symlink (Windows
#: without the privilege) skips only the tests that need one.
SYMLINKS_SUPPORTED = _symlinks_supported()


# --- Codex: the manifest's timeouts reach both kinds of server -------------------------

def test_codex_writes_timeouts_for_stdio_servers_and_keeps_their_env(tmp_path):
    renderer = make_renderer(tmp_path, {
        "browser": {"transport": "stdio", "command": "npx", "args": ["-y", "pw"], "tier": "core",
                    "env": {"MODE": "headless"}, "timeouts": {"startup": 30, "tool": 120}},
        "remote": {"transport": "http", "url": "https://example.com/mcp", "tier": "core",
                   "timeouts": {"startup": 15, "tool": 60}},
    })
    renderer.render_codex(write=True)
    parsed = tomllib.loads((tmp_path / "home/.codex/config.toml").read_text())["mcp_servers"]
    assert parsed["browser"]["startup_timeout_sec"] == 30.0
    assert parsed["browser"]["tool_timeout_sec"] == 120.0
    # A key written after the env header would have landed inside the env table.
    assert parsed["browser"]["env"] == {"MODE": "headless"}
    assert parsed["remote"]["startup_timeout_sec"] == 15.0
    assert parsed["remote"]["tool_timeout_sec"] == 60.0


# --- Claude: a file that only differs in formatting is not rewritten ----------------------

def claude_file(tmp_path) -> Path:
    return tmp_path / "home/.claude.json"


def write_like_claude_code(path: Path, data: dict) -> None:
    """How the CLI itself saves it: JSON.stringify(data, null, 2), non-ASCII raw, no final newline."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


ALPHA_AS_CLAUDE = {"type": "stdio", "command": "python", "args": ["-V"], "env": {}}


def test_claude_file_in_the_cli_own_format_is_left_alone(tmp_path):
    renderer = make_renderer(tmp_path)
    cfg = claude_file(tmp_path)
    write_like_claude_code(cfg, {"projects": {"/home/user/caffè": {"note": "perché"}},
                                 "mcpServers": {"alpha": ALPHA_AS_CLAUDE}})
    before = cfg.read_bytes()
    before_mtime = cfg.stat().st_mtime_ns
    renderer.render_claude(write=True)
    assert cfg.read_bytes() == before
    assert cfg.stat().st_mtime_ns == before_mtime
    assert backups(cfg) == []


def test_claude_real_change_is_written_once_with_a_backup_and_keeps_the_rest(tmp_path):
    renderer = make_renderer(tmp_path)
    cfg = claude_file(tmp_path)
    write_like_claude_code(cfg, {"projects": {"/home/user/caffè": {"note": "perché"}},
                                 "mcpServers": {"private": {"type": "stdio", "command": "mine"}}})
    renderer.render_claude(write=True)
    text = cfg.read_text(encoding="utf-8")
    data = json.loads(text)
    assert set(data["mcpServers"]) == {"private", "alpha"}
    assert data["projects"]["/home/user/caffè"]["note"] == "perché"
    assert "perché" in text and "\\u00e9" not in text
    assert len(backups(cfg)) == 1
    renderer.render_claude(write=True)
    assert len(backups(cfg)) == 1


@pytest.mark.parametrize("content", ['{"mcpServers": []}', "[1, 2]"])
def test_claude_unexpected_shape_stops_the_render_and_keeps_the_file(tmp_path, content):
    renderer = make_renderer(tmp_path)
    cfg = claude_file(tmp_path)
    cfg.write_text(content)
    with pytest.raises(ValueError):
        renderer.render_claude(write=True)
    assert cfg.read_text() == content


def test_claude_empty_file_is_treated_as_nothing_to_protect(tmp_path):
    renderer = make_renderer(tmp_path)
    cfg = claude_file(tmp_path)
    cfg.write_text("\n")
    renderer.render_claude(write=True)
    assert set(json.loads(cfg.read_text())["mcpServers"]) == {"alpha"}


def test_claude_save_landing_mid_render_is_not_lost(tmp_path, monkeypatch):
    """Claude Code and the Desktop app save session and trust state into this
    same file, and the save can land after the render has read the file and
    computed its `mcpServers`, while the write is still to come. The render
    re-reads the file at the last moment and re-applies only `mcpServers`, so
    that save survives.

    The save is injected from inside the table's computation on purpose: that
    is after every read the old code made and before the write, which is the
    window a re-read at the top of the render cannot cover.
    """
    renderer = make_renderer(tmp_path)
    cfg = claude_file(tmp_path)
    write_like_claude_code(cfg, {"mcpServers": {}})
    real_drop = renderer._drop_unmounted

    def cli_saves_after_the_table_is_computed(mcp_servers, servers, cli_target=""):
        real_drop(mcp_servers, servers, cli_target=cli_target)
        write_like_claude_code(cfg, {"projects": {"/home/user/x": {"trusted": True}}})

    monkeypatch.setattr(renderer, "_drop_unmounted", cli_saves_after_the_table_is_computed)
    renderer.render_claude(write=True)

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["projects"] == {"/home/user/x": {"trusted": True}}
    assert set(data["mcpServers"]) == {"alpha"}


# --- Antigravity: a real file at a consumer path is merged and backed up, never deleted ------

def consumer(tmp_path, directory) -> Path:
    return tmp_path / "home/.gemini" / directory / "mcp_config.json"


def _render_agy_with_and_without_openclaw(tmp_path, monkeypatch, gateway_targets):
    servers = {
        "browser": {"transport": "stdio", "command": "npx", "args": ["-y", "pw"], "tier": "core"},
        "notes": {"transport": "stdio", "command": "npx", "args": ["-y", "notes"], "exposure": "lazy"},
        "lazy-mcp": {"transport": "stdio", "command": "python3", "args": ["lazy-mcp.py"], "tier": "core",
                     "targets": gateway_targets},
    }
    renderer = make_renderer(tmp_path, servers)
    config = tmp_path / "openclaw.json"
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(config))
    canonical = tmp_path / "home/.gemini/config/mcp_config.json"

    renderer.render_antigravity(write=True)
    before = json.loads(canonical.read_text(encoding="utf-8"))["mcpServers"]

    config.write_text(json.dumps({"plugins": {
        "allow": ["google-antigravity-cli"],
        "entries": {"google-antigravity-cli": {"enabled": True}},
    }}), encoding="utf-8")
    renderer.render_antigravity(write=True)
    return before, json.loads(canonical.read_text(encoding="utf-8"))["mcpServers"]


def test_agy_inside_openclaw_reaches_lazy_servers_through_the_gateway_openclaw_bridges(tmp_path, monkeypatch):
    """The plugin starts the same `agy` from the same home and bridges OpenClaw's own servers into it
    as `openclaw__<name>`, the gateway included when OpenClaw mounts it.

    A lazy server copied in as a direct entry as well was started by agy at every turn: a remote
    one that did not connect left the turn waiting until the watchdog killed it, and it sat
    outside the `tools.deny` OpenClaw applies to its own servers (finance, 2026-11-07).
    """
    before, after = _render_agy_with_and_without_openclaw(
        tmp_path, monkeypatch, ["claude", "codex", "antigravity", "opencode", "openclaw"])

    assert "lazy-mcp" in before and "notes" not in before
    assert "notes" not in after, "behind the gateway OpenClaw bridges in, never a second direct copy"
    assert "lazy-mcp" not in after, "the gateway reaches agy as OpenClaw's bridged copy, not this file"
    assert "browser" in after


def test_agy_inside_openclaw_without_a_gateway_still_gets_lazy_servers_directly(tmp_path, monkeypatch):
    """Where OpenClaw mounts no gateway, a direct entry is the only road to a lazy server."""
    _before, after = _render_agy_with_and_without_openclaw(
        tmp_path, monkeypatch, ["claude", "codex", "antigravity", "opencode"])

    assert "notes" in after, "OpenClaw mounts it directly, and agy inside OpenClaw must reach it"
    assert "lazy-mcp" not in after
    assert "browser" in after


def test_the_doctor_expects_of_agy_inside_openclaw_what_the_render_writes(tmp_path, monkeypatch):
    """The render leaves the gateway out of agy's file when OpenClaw drives agy; the doctor
    asked the plain antigravity list and reported `antigravity: missing lazy-mcp` forever."""
    from nexgen_core.checks import mcp_checks

    _render_agy_with_and_without_openclaw(
        tmp_path, monkeypatch, ["claude", "codex", "antigravity", "opencode", "openclaw"])
    outcome = mcp_checks.check_mcp_configs_rendered(_vault(tmp_path), tmp_path / "home")

    # OpenClaw's own file is a stub in this fixture; only agy's verdict is under test.
    assert "antigravity: missing" not in outcome.message, outcome.message


@pytest.mark.skipif(not SYMLINKS_SUPPORTED, reason="this machine may not create symlinks")
def test_antigravity_real_file_is_backed_up_and_its_servers_are_kept(tmp_path):
    renderer = make_renderer(tmp_path)
    variant = consumer(tmp_path, "antigravity-cli")
    variant.parent.mkdir(parents=True)
    own = {"mcpServers": {"hand-added": {"command": "mine", "args": ["--x"], "env": {}}}}
    variant.write_text(json.dumps(own))
    renderer.render_antigravity(write=True)
    canonical = tmp_path / "home/.gemini/config/mcp_config.json"
    assert set(json.loads(canonical.read_text())["mcpServers"]) == {"alpha", "hand-added"}
    assert variant.is_symlink() and variant.resolve() == canonical.resolve()
    saved = [json.loads(p.read_text()) for p in backups(variant)]
    assert saved == [own]


@pytest.mark.skipif(not SYMLINKS_SUPPORTED, reason="this machine may not create symlinks")
def test_antigravity_retired_server_in_a_copy_does_not_come_back(tmp_path):
    renderer = make_renderer(tmp_path)
    renderer.manifest_path.write_text(yaml.safe_dump({
        "servers": {"alpha": {"transport": "stdio", "command": "python", "tier": "core"}},
        "retired_servers": ["old"],
    }))
    variant = consumer(tmp_path, "antigravity-ide")
    variant.parent.mkdir(parents=True)
    variant.write_text(json.dumps({"mcpServers": {"old": {"command": "x"}}}))
    renderer.render_antigravity(write=True)
    canonical = tmp_path / "home/.gemini/config/mcp_config.json"
    assert "old" not in json.loads(canonical.read_text())["mcpServers"]


def test_antigravity_copy_fallback_is_idempotent(tmp_path, monkeypatch):
    """Where links cannot be made (Windows without privileges) the copy is made once, not every cycle."""
    renderer = make_renderer(tmp_path)

    def no_links(link, target):
        raise OSError("symlink privilege not held")

    from nexgen_core.mcp_render import antigravity
    monkeypatch.setattr(antigravity, "publish_symlink", no_links)
    renderer.render_antigravity(write=True)
    variant = consumer(tmp_path, "antigravity-ide")
    assert variant.is_file() and not variant.is_symlink()
    canonical = tmp_path / "home/.gemini/config/mcp_config.json"
    assert variant.read_bytes() == canonical.read_bytes()
    mtime = variant.stat().st_mtime_ns
    renderer.render_antigravity(write=True)
    renderer.render_antigravity(write=True)
    assert variant.stat().st_mtime_ns == mtime
    assert backups(variant) == []


# --- Antigravity: the officially read path holds the real file --------------------------------

def test_a_fresh_machine_gets_the_officially_read_file_as_the_real_one(tmp_path):
    """Google's MCP doc names `~/.gemini/config/mcp_config.json` for the app and `agy` alike.

    A link there is what silently lost the configuration: an app that saves the
    file replaces the link with a copy of its own, and the two drift apart.
    """
    renderer = make_renderer(tmp_path)
    renderer.render_antigravity(write=True)

    canonical = tmp_path / "home/.gemini/config/mcp_config.json"
    assert canonical.is_file() and not canonical.is_symlink()
    assert set(json.loads(canonical.read_text(encoding="utf-8"))["mcpServers"]) == {"alpha"}
    for directory in ("antigravity", "antigravity-cli", "antigravity-ide"):
        copy = consumer(tmp_path, directory)
        assert copy.read_bytes() == canonical.read_bytes(), directory


@pytest.mark.skipif(not SYMLINKS_SUPPORTED, reason="this machine may not create symlinks")
def test_every_other_folder_antigravity_reads_points_at_that_file(tmp_path):
    renderer = make_renderer(tmp_path)
    renderer.render_antigravity(write=True)

    canonical = tmp_path / "home/.gemini/config/mcp_config.json"
    for directory in ("antigravity", "antigravity-cli", "antigravity-ide"):
        copy = consumer(tmp_path, directory)
        assert copy.is_symlink(), directory
        assert copy.resolve() == canonical.resolve(), directory


@pytest.mark.skipif(not SYMLINKS_SUPPORTED, reason="this machine may not create symlinks")
def test_the_old_layout_is_migrated_without_losing_a_server(tmp_path):
    """The previous release kept the real file in `antigravity/` and linked `config/` to it."""
    renderer = make_renderer(tmp_path)
    old = {"mcpServers": {"hand-added": {"command": "mine", "args": [], "env": {}}}}
    legacy = consumer(tmp_path, "antigravity")
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(old))
    canonical = consumer(tmp_path, "config")
    canonical.parent.mkdir(parents=True)
    canonical.symlink_to(legacy)
    cli_copy = consumer(tmp_path, "antigravity-cli")
    cli_copy.parent.mkdir(parents=True)
    cli_copy.symlink_to(legacy)

    renderer.render_antigravity(write=False)
    assert canonical.is_symlink(), "a preview never migrates anything"

    renderer.render_antigravity(write=True)

    assert canonical.is_file() and not canonical.is_symlink()
    assert set(json.loads(canonical.read_text(encoding="utf-8"))["mcpServers"]) == {"alpha", "hand-added"}
    assert legacy.is_symlink() and legacy.resolve() == canonical.resolve()
    assert cli_copy.is_symlink() and cli_copy.resolve() == canonical.resolve()
    saved = [json.loads(path.read_text(encoding="utf-8")) for path in backups(legacy)]
    assert saved == [old]

    from nexgen_core.checks import mcp_checks
    from nexgen_core.report import Severity

    assert mcp_checks.check_antigravity_fanout(tmp_path / "vault", renderer.home).severity == Severity.OK

    renderer.render_antigravity(write=True)
    assert [json.loads(path.read_text(encoding="utf-8")) for path in backups(legacy)] == [old]
    assert len(backups(canonical)) == 1, "a second pass changes nothing and takes no new backup"


@pytest.mark.skipif(not SYMLINKS_SUPPORTED, reason="this machine may not create symlinks")
def test_a_link_whose_old_target_was_deleted_does_not_recreate_the_old_layout(tmp_path):
    """Uninstalling the app can remove `antigravity/` while `config/` still links into it."""
    renderer = make_renderer(tmp_path)
    canonical = consumer(tmp_path, "config")
    canonical.parent.mkdir(parents=True)
    canonical.symlink_to(consumer(tmp_path, "antigravity"))

    renderer.render_antigravity(write=True)

    assert canonical.is_file() and not canonical.is_symlink()
    assert set(json.loads(canonical.read_text(encoding="utf-8"))["mcpServers"]) == {"alpha"}
    legacy = consumer(tmp_path, "antigravity")
    assert legacy.is_symlink() and legacy.resolve() == canonical.resolve()


def test_two_real_files_that_disagree_stop_the_render_and_are_left_alone(tmp_path):
    """Choosing one of them would drop whatever only the other held: both paths are named instead."""
    renderer = make_renderer(tmp_path)
    canonical = consumer(tmp_path, "config")
    legacy = consumer(tmp_path, "antigravity")
    canonical.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    canonical_text = json.dumps({"mcpServers": {"from-config": {"command": "a"}}})
    legacy_text = json.dumps({"mcpServers": {"from-antigravity": {"command": "b"}}})
    canonical.write_text(canonical_text)
    legacy.write_text(legacy_text)

    with pytest.raises(ValueError) as caught:
        renderer.render_antigravity(write=True)

    assert str(canonical) in str(caught.value) and str(legacy) in str(caught.value)
    assert canonical.read_text(encoding="utf-8") == canonical_text
    assert legacy.read_text(encoding="utf-8") == legacy_text
    assert backups(canonical) == [] and backups(legacy) == []


def test_a_stale_copy_that_repeats_the_canonical_servers_is_healed_not_refused(tmp_path):
    """Where links cannot be created the fan-out leaves a copy, and the app saving the
    canonical file leaves that copy behind: both real, different bytes, nothing lost by
    re-publishing it. Refusing stopped every render until a human moved a file aside."""
    renderer = make_renderer(tmp_path)
    renderer.render_antigravity(write=True)
    canonical = consumer(tmp_path, "config")
    legacy = consumer(tmp_path, "antigravity")
    if legacy.is_symlink():
        legacy.unlink()
    stale = json.dumps({"mcpServers": {"alpha": {"command": "python", "args": ["-V"]}}}, indent=4)
    legacy.write_text(stale, encoding="utf-8")
    assert canonical.read_text(encoding="utf-8") != stale

    renderer.render_antigravity(write=True)

    assert legacy.read_bytes() == canonical.read_bytes()
    assert set(json.loads(legacy.read_text(encoding="utf-8"))["mcpServers"]) == {"alpha"}
    assert [path.read_text(encoding="utf-8") for path in backups(legacy)] == [stale]


@pytest.mark.skipif(os.name == "nt", reason="symlinks require privileges")
def test_publish_symlink_failure_leaves_what_was_there(tmp_path, monkeypatch):
    old = tmp_path / "keep-me"
    old.write_text("original")
    link = tmp_path / "link"
    link.write_text("a real file")
    real_replace = files.os.replace

    def fail(src, dst):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(files.os, "replace", fail)
    with pytest.raises(OSError):
        files.publish_symlink(link, old)
    monkeypatch.setattr(files.os, "replace", real_replace)
    assert link.read_text() == "a real file"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep-me", "link"]


# --- OpenCode: a commented opencode.json is read like the .jsonc one ------------------------

COMMENTED = '''{
  // my own notes, OpenCode reads this file with comments allowed
  "theme": "dark", /* keep */
  "mcp": {}
}
'''


def test_opencode_json_with_comments_renders_and_keeps_them(tmp_path):
    renderer = make_renderer(tmp_path)
    cfg = tmp_path / "home/.config/opencode/opencode.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(COMMENTED)
    renderer.render_opencode(write=True)
    text = cfg.read_text()
    assert "// my own notes" in text and "/* keep */" in text
    from nexgen_core.jsonc import parse_jsonc
    data = parse_jsonc(text)
    assert data["theme"] == "dark"
    assert "alpha" in data["mcp"]["servers"]


def test_opencode_empty_file_still_gets_plain_json(tmp_path):
    renderer = make_renderer(tmp_path)
    cfg = tmp_path / "home/.config/opencode/opencode.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("")
    renderer.render_opencode(write=True)
    assert "alpha" in json.loads(cfg.read_text())["mcp"]["servers"]


# --- A managed server keeps the state the app keeps beside it --------------------------------

#: The per-server state the apps write and the engine never does.
APP_STATE = {"disabled": True, "enabledTools": ["read"], "tools": {"read": {"eager": True}}}


def test_claude_keeps_the_app_state_of_a_server_it_manages(tmp_path):
    """Turning a server off in the Claude screen survived only until the next guard cycle."""
    renderer = make_renderer(tmp_path)
    cfg = claude_file(tmp_path)
    write_like_claude_code(cfg, {"mcpServers": {"alpha": {
        "type": "stdio", "command": "old", "args": [], "env": {}, **APP_STATE}}})
    renderer.render_claude(write=True)
    entry = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["alpha"]
    assert entry == {"disabled": True, "enabledTools": ["read"],
                     "tools": {"read": {"eager": True}},
                     "type": "stdio", "command": "python", "args": ["-V"], "env": {}}


def test_antigravity_keeps_the_app_state_of_a_server_it_manages(tmp_path):
    renderer = make_renderer(tmp_path)
    canonical = tmp_path / "home/.gemini/config/mcp_config.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(json.dumps({"mcpServers": {"alpha": {
        "command": "old", "args": [], "env": {}, "timeoutSeconds": 600, **APP_STATE}}}))
    renderer.render_antigravity(write=True)
    entry = json.loads(canonical.read_text(encoding="utf-8"))["mcpServers"]["alpha"]
    assert entry == {"disabled": True, "enabledTools": ["read"],
                     "tools": {"read": {"eager": True}}, "timeoutSeconds": 600,
                     "command": "python", "args": ["-V"], "env": {}}


def test_opencode_keeps_the_app_state_of_a_server_it_manages(tmp_path):
    renderer = make_renderer(tmp_path)
    cfg = renderer.opencode_config_path()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcp": {"servers": {"alpha": {
        "type": "local", "command": ["old"], "disabled": True}}}}), encoding="utf-8")
    renderer.render_opencode(write=True)
    entry = json.loads(cfg.read_text(encoding="utf-8"))["mcp"]["servers"]["alpha"]
    assert entry == {"disabled": True, "type": "local", "command": ["python", "-V"]}


def test_codex_keeps_the_app_state_of_a_server_it_manages(tmp_path):
    """Codex keeps its per-server state as TOML keys and per-tool sub-tables."""
    renderer = make_renderer(tmp_path)
    cfg = tmp_path / "home/.codex/config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.alpha]\ncommand = "old"\nargs = []\n'
                   'disabled_tools = ["write_note"]\ndefault_tools_approval_mode = "approve"\n\n'
                   '[mcp_servers.alpha.tools.read]\napproval_mode = "approve"\n',
                   encoding="utf-8")
    renderer.render_codex(write=True)
    entry = tomllib.loads(cfg.read_text(encoding="utf-8"))["mcp_servers"]["alpha"]
    assert entry == {"command": "python", "args": ["-V"], "disabled_tools": ["write_note"],
                     "default_tools_approval_mode": "approve",
                     "tools": {"read": {"approval_mode": "approve"}}}


def test_antigravity_save_landing_mid_render_is_not_lost(tmp_path, monkeypatch):
    """The app's MCP screen saves into this file too, and the save can land after the
    render read it and computed `mcpServers`, while the write is still to come. The
    render re-reads the file at the last moment and re-applies only that one table."""
    renderer = make_renderer(tmp_path)
    canonical = tmp_path / "home/.gemini/config/mcp_config.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    real_drop = renderer._drop_unmounted

    def app_saves_after_the_table_is_computed(mcp_servers, servers, cli_target=""):
        real_drop(mcp_servers, servers, cli_target=cli_target)
        canonical.write_text(json.dumps({"mcpServers": {}, "appPreference": {"theme": "dark"}}),
                            encoding="utf-8")

    monkeypatch.setattr(renderer, "_drop_unmounted", app_saves_after_the_table_is_computed)
    renderer.render_antigravity(write=True)

    data = json.loads(canonical.read_text(encoding="utf-8"))
    assert data["appPreference"] == {"theme": "dark"}
    assert set(data["mcpServers"]) == {"alpha"}


# --- Every render: a configuration file with a BOM is still that configuration --------------

def test_a_bom_in_a_config_file_does_not_stop_any_render(tmp_path):
    """An editor that writes a BOM (PowerShell's `Set-Content -Encoding utf8`) left a file
    the render refused as invalid JSON, so the CLI's MCP configuration stopped updating
    until someone guessed why."""
    renderer = make_renderer(tmp_path)
    kept = {"theme": "dark", "mcpServers": {"mine": {"command": "keep-me"}}}
    for cli, relative in (("claude", ".claude.json"), ("antigravity", ".gemini/config/mcp_config.json")):
        path = renderer.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(kept), encoding="utf-8-sig")
    opencode = renderer.home / ".config/opencode/opencode.json"
    opencode.parent.mkdir(parents=True)
    opencode.write_text(json.dumps({"theme": "dark", "mcp": {"servers": {
        "mine": {"type": "local", "command": ["keep-me"], "enabled": True},
    }}}), encoding="utf-8-sig")

    renderer.render_claude(write=True)
    renderer.render_antigravity(write=True)
    renderer.render_opencode(write=True)

    for relative in (".claude.json", ".gemini/config/mcp_config.json"):
        data = json.loads((renderer.home / relative).read_text(encoding="utf-8-sig"))
        assert data["mcpServers"]["mine"] == {"command": "keep-me"}, relative
        assert "alpha" in data["mcpServers"], relative
    rendered = json.loads(opencode.read_text(encoding="utf-8-sig"))
    assert rendered["theme"] == "dark"
    assert rendered["mcp"]["servers"]["mine"]["command"] == ["keep-me"]
    assert "alpha" in rendered["mcp"]["servers"]


# --- Doctor: it asks the renderer, in preview, instead of comparing names only -----------------

def _vault(tmp_path) -> Path:
    return tmp_path / "vault"


def test_after_apply_the_doctor_sees_nothing_to_change_in_any_cli(tmp_path):
    from nexgen_core.checks import mcp_checks
    from nexgen_core.report import Severity

    renderer = make_renderer(tmp_path, {
        "alpha": {"transport": "stdio", "command": sys.executable, "args": ["-V"], "tier": "core",
                  "env": {"A": "1"}, "timeouts": {"startup": 20, "tool": 90}},
        "remote": {"transport": "http", "url": "https://example.com/mcp", "tier": "core"},
    })
    renderer.render_all(write=True)
    outcome = mcp_checks.check_mcp_content_drift(_vault(tmp_path), tmp_path / "home", tmp_path / "engine")
    assert outcome.severity == Severity.OK, outcome.message


def test_doctor_names_the_server_whose_entry_would_change_and_writes_nothing(tmp_path):
    from nexgen_core.checks import mcp_checks
    from nexgen_core.report import Severity

    renderer = make_renderer(tmp_path)
    renderer.render_all(write=True)
    home = tmp_path / "home"
    snapshot = {p: p.read_bytes() for p in (home / ".claude.json", home / ".codex/config.toml")}
    renderer.manifest_path.write_text(yaml.safe_dump({"servers": {
        "alpha": {"transport": "stdio", "command": "python", "args": ["-V", "--changed"], "tier": "core"},
    }}))
    outcome = mcp_checks.check_mcp_content_drift(_vault(tmp_path), home, tmp_path / "engine")
    assert outcome.severity == Severity.WARN
    for cli in ("claude", "codex", "antigravity", "opencode"):
        assert f"{cli}: alpha" in outcome.message
    assert {p: p.read_bytes() for p in snapshot} == snapshot
    assert outcome.remedy() is True
    assert mcp_checks.check_mcp_content_drift(_vault(tmp_path), home, tmp_path / "engine").severity == Severity.OK


def test_doctor_content_check_ignores_a_cli_that_was_never_launched(tmp_path):
    from nexgen_core.checks import mcp_checks
    from nexgen_core.report import Severity

    make_renderer(tmp_path)
    outcome = mcp_checks.check_mcp_content_drift(_vault(tmp_path), tmp_path / "home", tmp_path / "engine")
    assert outcome.severity == Severity.OK
    assert list((tmp_path / "home").iterdir()) == []


def test_doctor_flags_a_server_whose_program_is_not_installed(tmp_path):
    from nexgen_core.checks import mcp_checks
    from nexgen_core.report import Severity

    make_renderer(tmp_path, {
        "present": {"transport": "stdio", "command": sys.executable, "tier": "core"},
        "absent": {"transport": "stdio", "command": "definitely-not-installed-xyz", "tier": "core"},
        "remote": {"transport": "http", "url": "https://example.com/mcp", "tier": "core"},
    })
    outcome = mcp_checks.check_mcp_commands(_vault(tmp_path), tmp_path / "home")
    assert outcome.severity == Severity.WARN
    assert "absent" in outcome.message and "present" not in outcome.message and "remote" not in outcome.message
