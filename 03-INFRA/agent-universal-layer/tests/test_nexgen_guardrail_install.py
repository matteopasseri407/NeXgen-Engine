"""How the guardrails are installed per CLI: the registration, the sidecar flags, the mediated bypass, the audit check."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REAL_VAULT
from nexgen_core.checks.guardrail_checks import check_guardrail_consulted
from nexgen_core.report import Severity
from nexgen_core.runtimes.antigravity import AntigravityRuntime
from nexgen_core.runtimes.base import GuardrailError
from nexgen_core.runtimes.claude import ClaudeRuntime
from nexgen_core.runtimes.opencode import OpenCodeRuntime

HOOKS = REAL_VAULT / "03-INFRA" / "agent-universal-layer" / "hooks"


@pytest.fixture(autouse=True)
def _no_ambient_binaries(monkeypatch):
    # is_installed() must come from the fixture's footprint, not from whatever the machine has.
    for module in ("claude", "codex", "opencode", "antigravity"):
        monkeypatch.setattr(f"nexgen_core.runtimes.{module}.shutil.which", lambda *_a, **_k: None)


def _body(tmp_path: Path, text: str = "// policy\n") -> Path:
    body = tmp_path / "guardrail-catastrophic.mjs"
    body.write_text(text, encoding="utf-8")
    return body


def _claude_home(tmp_path: Path, settings: dict | None = None) -> tuple[Path, Path]:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    path = home / ".claude" / "settings.json"
    path.write_text(json.dumps(settings or {}), encoding="utf-8")
    return home, path


def _opencode_home(tmp_path: Path, config: dict | None = None) -> tuple[Path, Path]:
    home = tmp_path / "home"
    path = home / ".config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(config or {}), encoding="utf-8")
    # The config file is not a valid signal that OpenCode is installed (the renderer writes it
    # either way); its own binary is.
    binary = home / ".opencode" / "bin" / "opencode"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    return home, path


# ------------------------------------------------------------------ Claude


def test_a_hook_registered_the_old_way_is_migrated_to_the_adapter_without_duplicating(tmp_path):
    """Before the adapter existed the body itself was the hook's command, and failed open."""
    home, settings = _claude_home(tmp_path)
    claude_dir = home / ".claude"
    old_command = f'node "{claude_dir / "guardrail-catastrophic.mjs"}"'
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": old_command, "timeout": 5}]},
        {"matcher": "*", "hooks": [{"type": "command", "command": "node someone-elses.mjs"}]},
    ]}}), encoding="utf-8")
    rt = ClaudeRuntime()

    action = rt.install_guardrail(home, _body(tmp_path), HOOKS)

    entries = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    commands = [h["command"] for e in entries for h in e["hooks"]]
    assert commands == [f'node "{claude_dir / "claude-guardrail-adapter.mjs"}"', "node someone-elses.mjs"]
    assert "spec updated" in action
    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None  # and it settles


def test_the_deployed_adapter_is_the_one_that_ships_and_leaves_no_staging_file(tmp_path):
    home, _settings = _claude_home(tmp_path)
    ClaudeRuntime().install_guardrail(home, _body(tmp_path), HOOKS)
    claude_dir = home / ".claude"
    for name in ("claude-guardrail-adapter.mjs", "nexgen-guardrail-core.mjs"):
        assert (claude_dir / name).read_bytes() == (HOOKS / name).read_bytes()
    assert not list(claude_dir.rglob("*.nexgen-tmp"))


def test_a_deploy_cut_off_midway_leaves_the_previous_adapter_intact(tmp_path, monkeypatch):
    """A truncated adapter does not parse, and a hook that fails to start is one Claude ignores."""
    home, _settings = _claude_home(tmp_path)
    rt = ClaudeRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    adapter = home / ".claude" / "claude-guardrail-adapter.mjs"
    adapter.write_text("// an older adapter\n", encoding="utf-8")
    monkeypatch.setattr(os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("x")))

    with pytest.raises(OSError):
        rt.install_guardrail(home, _body(tmp_path), HOOKS)

    assert adapter.read_text(encoding="utf-8") == "// an older adapter\n"


def test_a_missing_core_is_refused_before_anything_is_registered(tmp_path):
    home, settings = _claude_home(tmp_path)
    hooks = tmp_path / "hooks"
    shutil.copytree(HOOKS, hooks)
    (hooks / "nexgen-guardrail-core.mjs").unlink()
    before = settings.read_text(encoding="utf-8")

    with pytest.raises(GuardrailError, match="core"):
        ClaudeRuntime().install_guardrail(home, _body(tmp_path), hooks)

    assert settings.read_text(encoding="utf-8") == before


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_installed_claude_hook_blocks_when_the_installed_body_is_broken(tmp_path):
    """Python installs it, node runs it: the whole path a command takes, as Claude would drive it."""
    home, _settings = _claude_home(tmp_path)
    ClaudeRuntime().install_guardrail(home, _body(tmp_path, "process.exit(1);\n"), HOOKS)
    adapter = home / ".claude" / "claude-guardrail-adapter.mjs"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "permission_mode": "bypassPermissions"})

    done = subprocess.run(["node", str(adapter)], input=payload, capture_output=True, text=True, timeout=60)

    assert done.returncode == 0
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ------------------------------------------------------------------ OpenCode: the mediated bypass


def _install_and_bypass(tmp_path, config=None, *, guardrail=True):
    home, path = _opencode_home(tmp_path, config)
    rt = OpenCodeRuntime()
    if guardrail:
        rt.install_guardrail(home, _body(tmp_path), HOOKS)
    rt.apply_posture(home, "bypass")
    rules = {(r["action"], r["resource"]): r["effect"] for r in json.loads(path.read_text(encoding="utf-8"))["permissions"]}
    return home, path, rt, rules


def test_with_the_plugin_installed_bypass_asks_and_lets_the_plugin_answer(tmp_path):
    """permission.ask is only called for actions OpenCode is about to ask about: a shell rule
    saying allow left the plugin registered and unreachable, and nothing said so."""
    home, _path, rt, rules = _install_and_bypass(tmp_path)

    assert rules[("edit", "*")] == "allow" and rules[("shell", "*")] == "ask"
    sidecar = rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))
    assert sidecar["autoAllow"] is True and sidecar["strict"] is True
    assert rt.read_posture(home) == "bypass"  # the doctor still sees the posture the manifest declared


def test_without_the_plugin_bypass_is_rendered_as_before(tmp_path):
    _home, _path, _rt, rules = _install_and_bypass(tmp_path, guardrail=False)
    assert rules[("shell", "*")] == "allow"


def test_an_allow_written_before_the_plugin_existed_is_converted_not_left_unreachable(tmp_path):
    home, path = _opencode_home(tmp_path)
    rt = OpenCodeRuntime()
    rt.apply_posture(home, "bypass")  # an earlier cycle: no guardrail yet
    assert {(r["action"], r["resource"]): r["effect"] for r in json.loads(path.read_text(encoding="utf-8"))["permissions"]}[("shell", "*")] == "allow"

    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    rt.apply_posture(home, "bypass")

    rules = {(r["action"], r["resource"]): r["effect"] for r in json.loads(path.read_text(encoding="utf-8"))["permissions"]}
    assert rules[("shell", "*")] == "ask"
    assert rt.apply_posture(home, "bypass") is None  # and it settles


def test_an_explicit_user_deny_is_never_touched(tmp_path):
    _home, _path, _rt, rules = _install_and_bypass(
        tmp_path, {"permissions": [{"action": "shell", "resource": "*", "effect": "deny"}]})
    assert rules[("shell", "*")] == "deny"


def test_an_ask_posture_keeps_asking_and_the_plugin_does_not_answer_for_the_person(tmp_path):
    home, _path = _opencode_home(tmp_path)
    rt = OpenCodeRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    rt.apply_posture(home, "bypass")
    assert rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))["autoAllow"] is True

    rt.apply_posture(home, "accept-edits")

    sidecar = rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))
    assert sidecar["autoAllow"] is False and sidecar["strict"] is False
    assert rt.read_posture(home) == "accept-edits"


def test_reinstalling_the_guardrail_keeps_the_flags_the_posture_set(tmp_path):
    """The posture is applied after the guardrail, in another call; the next guardrail install
    must not undo it."""
    home, _path, rt, _rules = _install_and_bypass(tmp_path)
    rt.install_guardrail(home, _body(tmp_path, "// a newer policy\n"), HOOKS)
    sidecar = rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))
    assert sidecar["autoAllow"] is True and sidecar["strict"] is True


# ------------------------------------------------------------------ Antigravity


def test_antigravity_becomes_strict_under_bypass(tmp_path):
    home = tmp_path / "home"
    settings = home / ".gemini" / "antigravity" / "settings.json"
    from nexgen_core.paths import antigravity_settings

    settings = antigravity_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")
    rt = AntigravityRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    assert rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))["strict"] is False

    rt.apply_posture(home, "bypass")

    assert rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))["strict"] is True


# ------------------------------------------------------------------ the doctor's question


def _audit(path: Path, count: int, minutes_ago: float = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cli": "x", "at": (time.time() - minutes_ago * 60) * 1000, "count": count,
                                "last": "allow"}), encoding="utf-8")


def test_a_guardrail_nothing_ever_reaches_is_reported_as_undetermined_not_green(tmp_path):
    home, _path = _opencode_home(tmp_path)
    OpenCodeRuntime().install_guardrail(home, _body(tmp_path), HOOKS)

    outcomes = check_guardrail_consulted(home)

    assert [o.id for o in outcomes] == ["guardrail.consulted.opencode"]
    assert outcomes[0].severity == Severity.UNDETERMINED
    assert "never been consulted" in outcomes[0].message and "opencode" in outcomes[0].action


def test_a_consulted_guardrail_is_green_and_says_how_often(tmp_path):
    home, _path = _opencode_home(tmp_path)
    rt = OpenCodeRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    _audit(Path(rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))["auditFile"]), count=42)

    (outcome,) = check_guardrail_consulted(home)

    assert outcome.severity == Severity.OK and "42" in outcome.message


def test_a_cli_without_a_guardrail_installed_produces_no_outcome(tmp_path):
    home, _path = _opencode_home(tmp_path)  # configured, but no guardrail was ever installed
    assert check_guardrail_consulted(home) == []
