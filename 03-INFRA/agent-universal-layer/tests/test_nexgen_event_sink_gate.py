"""Retirement of the legacy event sink: cleanup on existing machines and no reinstall.

On a machine where an older version of the engine registered the event sink, the engine
removes it once and never reinstalls it.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.guard import GuardRunner  # noqa: E402
from nexgen_core.runtimes.antigravity import AntigravityRuntime  # noqa: E402
from nexgen_core.runtimes.base import EVENT_SINK_NAME  # noqa: E402
from nexgen_core.runtimes.claude import ClaudeRuntime  # noqa: E402
from nexgen_core.runtimes.codex import CodexRuntime  # noqa: E402
from nexgen_core.runtimes.openclaw import OpenClawRuntime  # noqa: E402
from nexgen_core.runtimes.opencode import OpenCodeRuntime  # noqa: E402

REAL_HOOKS = Path(__file__).resolve().parents[1] / "hooks"


@pytest.fixture(autouse=True)
def _no_ambient_binaries(monkeypatch):
    for mod in ("claude", "codex", "opencode", "antigravity", "openclaw"):
        monkeypatch.setattr(f"nexgen_core.runtimes.{mod}.shutil.which", lambda name: "/usr/bin/x" if name == "codex" else None)


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _json_c(path: Path):
    from nexgen_core.jsonc import parse_jsonc
    return parse_jsonc(path.read_text(encoding="utf-8"))


THEIR_HOOKS = {
    "Stop": [{"hooks": [{"type": "command", "command": "echo their-stop"}]}],
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "their-guard"}]}],
}


# --- Claude ---------------------------------------------------------------------------

def test_claude_remove_gives_back_exactly_what_was_there(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    deployed = home / ".claude" / EVENT_SINK_NAME
    deployed.write_text("// legacy sink", encoding="utf-8")

    with_sink = {
        "permissions": {"allow": ["Read"]},
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "echo their-stop"}]},
                {"hooks": [{"type": "command", "command": f'node "{deployed}" on_done claude'}]},
            ],
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "their-guard"}]},
                {"matcher": "*", "hooks": [{"type": "command", "command": f'node "{deployed}" on_step claude'}]},
            ],
        },
    }
    settings.write_text(json.dumps(with_sink), encoding="utf-8")

    runtime = ClaudeRuntime()
    action = runtime.remove_event_sink(home)
    assert action and "removed" in action
    assert _json(settings)["hooks"] == THEIR_HOOKS
    assert _json(settings)["permissions"] == {"allow": ["Read"]}
    assert not deployed.exists()

    # Second execution is an idempotent no-op
    assert runtime.remove_event_sink(home) is None


def test_claude_remove_keeps_a_group_that_also_holds_someone_elses_hook(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    mixed = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "mine"},
        {"type": "command", "command": 'node "/h/.claude/nexgen-event-sink.mjs" on_done claude'},
    ]}]}}
    settings.write_text(json.dumps(mixed), encoding="utf-8")
    ClaudeRuntime().remove_event_sink(home)
    assert _json(settings) == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}


def test_claude_remove_with_no_settings_is_nothing(tmp_path: Path):
    assert ClaudeRuntime().remove_event_sink(tmp_path / "home") is None


# --- Codex ----------------------------------------------------------------------------

def test_codex_remove_gives_back_what_was_there_and_drops_the_legacy_key(tmp_path: Path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    deployed = codex_dir / EVENT_SINK_NAME
    deployed.write_text("// legacy sink", encoding="utf-8")
    hooks_path = codex_dir / "hooks.json"
    with_sink = {
        "nexgen-event-sink": {"enabled": True},
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "echo their-stop"}]},
                {"hooks": [{"type": "command", "command": f'node "{deployed.as_posix()}" on_done codex'}]},
            ],
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "their-guard"}]},
                {"hooks": [{"type": "command", "command": f'node "{deployed.as_posix()}" on_step codex'}]},
            ],
        },
    }
    hooks_path.write_text(json.dumps(with_sink), encoding="utf-8")

    runtime = CodexRuntime()
    action = runtime.remove_event_sink(home)
    assert action and "removed" in action
    assert _json(hooks_path)["hooks"] == THEIR_HOOKS
    assert "nexgen-event-sink" not in _json(hooks_path)
    assert not deployed.exists()

    # Second execution is a no-op
    assert runtime.remove_event_sink(home) is None


# --- Antigravity ---------------------------------------------------------------------------

def test_antigravity_remove_drops_only_its_own_key_and_file(tmp_path: Path):
    home = tmp_path / "home dir"
    settings = home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")
    hooks_path = home / ".gemini" / "config" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    deployed = hooks_path.parent / EVENT_SINK_NAME
    deployed.write_text("// legacy sink", encoding="utf-8")
    hooks_path.write_text(json.dumps({
        "theirs": {"enabled": True},
        "nexgen-event-sink": {"enabled": True, "Stop": [], "PreToolUse": []},
    }), encoding="utf-8")

    runtime = AntigravityRuntime()
    action = runtime.remove_event_sink(home)
    assert action and "removed" in action
    assert _json(hooks_path) == {"theirs": {"enabled": True}}
    assert not deployed.exists()

    # Second execution is a no-op
    assert runtime.remove_event_sink(home) is None


# --- OpenCode -------------------------------------------------------------------------------

def test_opencode_remove_drops_the_plugin_entry_and_keeps_the_others_and_the_comments(tmp_path: Path):
    home = tmp_path / "home"
    config = home / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    deployed = config.parent / EVENT_SINK_NAME
    deployed.write_text("// legacy sink", encoding="utf-8")
    config.write_text(
        '{\n  // mine\n  "plugins": ["file:///mine.mjs", "' + deployed.as_posix() + '"],\n  "theme": "dark"\n}\n',
        encoding="utf-8",
    )

    runtime = OpenCodeRuntime()
    action = runtime.remove_event_sink(home)
    assert action and "removed" in action
    assert _json_c(config) == {"plugins": ["file:///mine.mjs"], "theme": "dark"}
    text = config.read_text(encoding="utf-8")
    assert "// mine" in text
    assert not deployed.exists()

    # Second execution is a no-op
    assert runtime.remove_event_sink(home) is None


# --- OpenClaw -------------------------------------------------------------------------------

def test_openclaw_remove_drops_legacy_files_and_cleans_config(tmp_path: Path):
    home = tmp_path / "home"
    state = home / ".openclaw"
    state.mkdir(parents=True)
    (state / "openclaw.json").write_text("{}", encoding="utf-8")
    plugin_dir = state / "extensions" / "nexgen"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "nexgen.config.json").write_text(json.dumps({"briefing": {"enabled": True}, "events": {"enabled": True}}), encoding="utf-8")
    (plugin_dir / EVENT_SINK_NAME).write_text("// legacy sink", encoding="utf-8")
    (plugin_dir / "events.mjs").write_text("// legacy events", encoding="utf-8")

    runtime = OpenClawRuntime()
    action = runtime.remove_event_sink(home)
    assert action and "removed" in action
    assert not (plugin_dir / EVENT_SINK_NAME).exists()
    assert not (plugin_dir / "events.mjs").exists()
    conf = json.loads((plugin_dir / "nexgen.config.json").read_text(encoding="utf-8"))
    assert "events" not in conf
    assert conf.get("briefing") == {"enabled": True}

    # Second execution is a no-op
    assert runtime.remove_event_sink(home) is None


# --- The guard: always removes legacy event sink, never installs it -----------------------

def _engine(tmp_path: Path) -> Path:
    engine = tmp_path / "engine" / "03-INFRA"
    shutil.copytree(REAL_HOOKS, engine / "agent-universal-layer" / "hooks")
    shutil.copytree(REAL_HOOKS.parent / "modules", engine / "agent-universal-layer" / "modules")
    return engine


def test_guard_runner_cleans_legacy_registration_and_never_reinstalls(tmp_path: Path, monkeypatch):
    from nexgen_core import modules

    monkeypatch.setattr(modules, "current_host", lambda: "this-host")
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    deployed = home / ".claude" / EVENT_SINK_NAME
    deployed.write_text("// legacy sink", encoding="utf-8")
    settings.write_text(json.dumps({
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": f'node "{deployed}" on_done claude'}]}],
        },
    }), encoding="utf-8")

    vault = tmp_path / "vault"
    vault.mkdir()
    runner = GuardRunner(vault_data=vault, engine_root=_engine(tmp_path), home=home)

    actions = runner.apply_runtime_permissions()
    assert any("event sink removed" in a for a in actions)
    assert not deployed.exists()
    assert "nexgen-event-sink.mjs" not in settings.read_text(encoding="utf-8")

    # Second execution does not report removal again and installs nothing
    actions2 = runner.apply_runtime_permissions()
    assert not any("event sink" in a for a in actions2)
    assert not deployed.exists()
    assert "nexgen-event-sink.mjs" not in settings.read_text(encoding="utf-8")
