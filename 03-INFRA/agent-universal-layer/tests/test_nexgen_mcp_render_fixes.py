"""The per-CLI configuration writers: what each one must not lose, rewrite or refuse."""
from __future__ import annotations

import json
import os
import sys
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


# --- Antigravity: a real file at a consumer path is merged and backed up, never deleted ------

def consumer(tmp_path, directory) -> Path:
    return tmp_path / "home/.gemini" / directory / "mcp_config.json"


@pytest.mark.skipif(os.name == "nt", reason="symlinks require privileges")
def test_antigravity_real_file_is_backed_up_and_its_servers_are_kept(tmp_path):
    renderer = make_renderer(tmp_path)
    variant = consumer(tmp_path, "antigravity-cli")
    variant.parent.mkdir(parents=True)
    own = {"mcpServers": {"hand-added": {"command": "mine", "args": ["--x"], "env": {}}}}
    variant.write_text(json.dumps(own))
    renderer.render_antigravity(write=True)
    canonical = tmp_path / "home/.gemini/antigravity/mcp_config.json"
    assert set(json.loads(canonical.read_text())["mcpServers"]) == {"alpha", "hand-added"}
    assert variant.is_symlink() and variant.resolve() == canonical.resolve()
    saved = [json.loads(p.read_text()) for p in backups(variant)]
    assert saved == [own]


@pytest.mark.skipif(os.name == "nt", reason="symlinks require privileges")
def test_antigravity_retired_server_in_a_copy_does_not_come_back(tmp_path):
    renderer = make_renderer(tmp_path)
    renderer.manifest_path.write_text(yaml.safe_dump({
        "servers": {"alpha": {"transport": "stdio", "command": "python", "tier": "core"}},
        "retired_servers": ["old"],
    }))
    variant = consumer(tmp_path, "config")
    variant.parent.mkdir(parents=True)
    variant.write_text(json.dumps({"mcpServers": {"old": {"command": "x"}}}))
    renderer.render_antigravity(write=True)
    canonical = tmp_path / "home/.gemini/antigravity/mcp_config.json"
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
    canonical = tmp_path / "home/.gemini/antigravity/mcp_config.json"
    assert variant.read_bytes() == canonical.read_bytes()
    mtime = variant.stat().st_mtime_ns
    renderer.render_antigravity(write=True)
    renderer.render_antigravity(write=True)
    assert variant.stat().st_mtime_ns == mtime
    assert backups(variant) == []


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
