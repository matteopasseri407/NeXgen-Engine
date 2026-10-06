"""The event-sink hook follows the modules a machine declared, and goes away with them.

It starts a Node process on every tool call of every session to emit an event that only a
voice cockpit listens for. It used to be registered on every machine with a permission
policy, with no way to take it back.
"""
from __future__ import annotations

import json
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.guard import GuardRunner  # noqa: E402
from nexgen_core.runtimes.antigravity import AntigravityRuntime  # noqa: E402
from nexgen_core.runtimes.claude import ClaudeRuntime  # noqa: E402
from nexgen_core.runtimes.codex import CodexRuntime  # noqa: E402
from nexgen_core.runtimes.opencode import OpenCodeRuntime  # noqa: E402

REAL_HOOKS = Path(__file__).resolve().parents[1] / "hooks"


@pytest.fixture(autouse=True)
def _no_ambient_binaries(monkeypatch):
    for mod in ("claude", "codex", "opencode", "antigravity"):
        monkeypatch.setattr(f"nexgen_core.runtimes.{mod}.shutil.which", lambda name: "/usr/bin/x" if name == "codex" else None)


@pytest.fixture
def sink_source(tmp_path):
    source = tmp_path / "nexgen-event-sink.mjs"
    source.write_text("// the sink\n", encoding="utf-8")
    return source


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


THEIR_HOOKS = {
    "Stop": [{"hooks": [{"type": "command", "command": "echo their-stop"}]}],
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "their-guard"}]}],
}


# --- Claude ---------------------------------------------------------------------------

def test_claude_remove_gives_back_exactly_what_was_there(tmp_path, sink_source):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = {"permissions": {"allow": ["Read"]}, "hooks": json.loads(json.dumps(THEIR_HOOKS))}
    settings.write_text(json.dumps(original), encoding="utf-8")
    runtime = ClaudeRuntime()
    assert runtime.install_event_sink(home, sink_source)
    assert _json(settings) != original
    assert runtime.remove_event_sink(home)
    assert _json(settings) == original
    assert not (home / ".claude" / "nexgen-event-sink.mjs").exists()
    assert runtime.remove_event_sink(home) is None


def test_claude_remove_keeps_a_group_that_also_holds_someone_elses_hook(tmp_path):
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


def test_claude_remove_with_no_settings_is_nothing(tmp_path):
    assert ClaudeRuntime().remove_event_sink(tmp_path / "home") is None


# --- Codex ----------------------------------------------------------------------------

def test_codex_remove_gives_back_what_was_there_and_drops_the_legacy_key(tmp_path, sink_source):
    home = tmp_path / "home"
    hooks_path = home / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    original = {"hooks": json.loads(json.dumps(THEIR_HOOKS))}
    hooks_path.write_text(json.dumps(original), encoding="utf-8")
    runtime = CodexRuntime()
    runtime.install_event_sink(home, sink_source)
    data = _json(hooks_path)
    data["nexgen-event-sink"] = {"enabled": True}
    hooks_path.write_text(json.dumps(data), encoding="utf-8")
    assert runtime.remove_event_sink(home)
    assert _json(hooks_path) == original
    assert not (home / ".codex" / "nexgen-event-sink.mjs").exists()


def test_codex_install_quotes_the_path_and_rewrites_an_older_unquoted_entry(tmp_path, sink_source):
    home = tmp_path / "home dir"
    hooks_path = home / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    deployed = (home / ".codex" / "nexgen-event-sink.mjs").as_posix()
    hooks_path.write_text(json.dumps({"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": f"node {deployed} on_done codex", "timeout": 5}]}],
    }}), encoding="utf-8")
    CodexRuntime().install_event_sink(home, sink_source)
    data = _json(hooks_path)
    commands = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert commands == [f'node "{deployed}" on_done codex']
    step = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert step == [f'node "{deployed}" on_step codex']
    assert CodexRuntime().install_event_sink(home, sink_source) is None


# --- Antigravity ---------------------------------------------------------------------------

def test_antigravity_remove_drops_only_its_own_key_and_file(tmp_path, sink_source):
    home = tmp_path / "home dir"
    settings = home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")
    hooks_path = home / ".gemini" / "config" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(json.dumps({"theirs": {"enabled": True}}), encoding="utf-8")
    runtime = AntigravityRuntime()
    runtime.install_event_sink(home, sink_source)
    entry = _json(hooks_path)["nexgen-event-sink"]
    assert all(f'node "{(hooks_path.parent / "nexgen-event-sink.mjs").as_posix()}"' in c["command"]
               for c in entry["Stop"])
    assert runtime.remove_event_sink(home)
    assert _json(hooks_path) == {"theirs": {"enabled": True}}
    assert not (hooks_path.parent / "nexgen-event-sink.mjs").exists()


# --- OpenCode -------------------------------------------------------------------------------

def test_opencode_remove_drops_the_plugin_entry_and_keeps_the_others_and_the_comments(tmp_path, sink_source):
    home = tmp_path / "home"
    config = home / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text('{\n  // mine\n  "plugins": ["file:///mine.mjs"],\n  "theme": "dark"\n}\n', encoding="utf-8")
    runtime = OpenCodeRuntime()
    runtime.install_event_sink(home, sink_source)
    assert any("nexgen-event-sink.mjs" in p for p in _json_c(config)["plugins"])
    assert runtime.remove_event_sink(home)
    text = config.read_text(encoding="utf-8")
    assert "// mine" in text
    assert _json_c(config) == {"plugins": ["file:///mine.mjs"], "theme": "dark"}
    assert not (config.parent / "nexgen-event-sink.mjs").exists()


def _json_c(path: Path):
    from nexgen_core.jsonc import parse_jsonc
    return parse_jsonc(path.read_text(encoding="utf-8"))


# --- The guard: install only when a declared module needs it -----------------------------

def _engine(tmp_path: Path) -> Path:
    engine = tmp_path / "engine" / "03-INFRA"
    shutil.copytree(REAL_HOOKS, engine / "agent-universal-layer" / "hooks")
    shutil.copytree(REAL_HOOKS.parent / "modules", engine / "agent-universal-layer" / "modules")
    return engine


def _voice_module(tmp_path: Path, gate: str = "") -> Path:
    source = tmp_path / "voice"
    source.mkdir()
    (source / "nexgen-module.yaml").write_text(textwrap.dedent(f"""
        schema_version: 2
        modules:
          voice:
            label: "Voice"
            kind: feature
            states: [absent, local]
            {gate}
            provides:
              runtime_hooks: [event_sink]
        """), encoding="utf-8")
    return source


def _declare(vault: Path, home_host: str, source: Path | None, state: str) -> None:
    body = {"schema_version": 2, "hosts": {home_host: {"modules": {"voice": state}}}}
    if source is not None:
        body["hosts"][home_host]["external"] = [str(source)]
    import yaml
    path = vault / "03-INFRA" / "agent-universal-layer" / "modules.state.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


@pytest.fixture
def machine(tmp_path, monkeypatch):
    from nexgen_core import modules

    monkeypatch.setattr(modules, "current_host", lambda: "this-host")
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"hooks": json.loads(json.dumps(THEIR_HOOKS))}), encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    runner = GuardRunner(vault_data=vault, engine_root=_engine(tmp_path), home=home)
    return runner, vault, settings, tmp_path


def _has_sink(settings: Path) -> bool:
    return "nexgen-event-sink.mjs" in settings.read_text(encoding="utf-8")


def test_a_machine_with_no_voice_module_does_not_get_the_hook(machine):
    runner, vault, settings, tmp_path = machine
    runner.apply_runtime_permissions()
    assert not _has_sink(settings)


def test_a_declared_voice_module_gets_the_hook_even_without_a_permission_policy(machine):
    runner, vault, settings, tmp_path = machine
    _declare(vault, "this-host", _voice_module(tmp_path), "local")
    actions = runner.apply_runtime_permissions()
    assert _has_sink(settings), actions


def test_declaring_the_module_absent_takes_the_hook_back(machine):
    runner, vault, settings, tmp_path = machine
    source = _voice_module(tmp_path)
    _declare(vault, "this-host", source, "local")
    runner.apply_runtime_permissions()
    assert _has_sink(settings)
    _declare(vault, "this-host", source, "absent")
    runner.apply_runtime_permissions()
    assert not _has_sink(settings)
    assert _json(settings)["hooks"] == THEIR_HOOKS


def test_unreadable_module_state_changes_nothing(machine):
    runner, vault, settings, tmp_path = machine
    source = _voice_module(tmp_path)
    _declare(vault, "this-host", source, "local")
    runner.apply_runtime_permissions()
    path = vault / "03-INFRA" / "agent-universal-layer" / "modules.state.yaml"
    path.write_text("hosts: [not, a, map\n", encoding="utf-8")
    runner.apply_runtime_permissions()
    assert _has_sink(settings)


def test_a_module_held_back_by_a_missing_env_gate_keeps_its_hook(machine, monkeypatch):
    """A timer without the user's tokens sees the module as inactive; that is not a removal."""
    runner, vault, settings, tmp_path = machine
    monkeypatch.delenv("NEXGEN_TEST_VOICE_GATE", raising=False)
    source = _voice_module(tmp_path, gate="env_gates: [NEXGEN_TEST_VOICE_GATE]")
    _declare(vault, "this-host", source, "local")
    monkeypatch.setenv("NEXGEN_TEST_VOICE_GATE", "1")
    runner.apply_runtime_permissions()
    assert _has_sink(settings)
    monkeypatch.delenv("NEXGEN_TEST_VOICE_GATE")
    runner.apply_runtime_permissions()
    assert _has_sink(settings)
