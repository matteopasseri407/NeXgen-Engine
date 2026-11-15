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
from nexgen_core.checks.guardrail_checks import (
    check_codex_guardrail_trust,
    check_guardrail_consulted,
    check_permission_posture,
)
from nexgen_core.report import Severity
from nexgen_core.runtimes.antigravity import AntigravityRuntime
from nexgen_core.runtimes.base import GuardrailError
from nexgen_core.runtimes.claude import ClaudeRuntime
from nexgen_core.runtimes.codex import STALE, TRUSTED, UNTRUSTED, CodexRuntime, guardrail_trust
from nexgen_core.runtimes.opencode import OpenCodeRuntime

HOOKS = REAL_VAULT / "03-INFRA" / "agent-universal-layer" / "hooks"


@pytest.fixture(autouse=True)
def _no_ambient_binaries(monkeypatch):
    # is_installed() must come from the fixture's footprint, not from whatever the machine has.
    for module in ("claude", "codex", "opencode", "antigravity"):
        monkeypatch.setattr(f"nexgen_core.runtimes.{module}.shutil.which", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _opencode_v2(opencode_dialect):
    """Every OpenCode case below proves the V2 writer; the V1 case pins its own dialect."""
    opencode_dialect("2.2.0")


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


def _registered_command(home: Path) -> str:
    return f'node "{home / ".claude" / "claude-guardrail-adapter.mjs"}"'


def test_a_hook_registered_matching_only_bash_is_upgraded_to_both_shell_tools(tmp_path):
    """On Windows Claude runs shell commands through the PowerShell tool, and `Bash` never matches it.

    An install written by an older engine carries the right handler under a matcher that leaves
    the PowerShell tool unchecked: it is repaired in place, not counted as already registered and
    not duplicated beside the repaired group.
    """
    home, settings = _claude_home(tmp_path)
    command = _registered_command(home)
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": 10}]},
        {"matcher": "*", "hooks": [{"type": "command", "command": "node someone-elses.mjs"}]},
    ]}}), encoding="utf-8")
    rt = ClaudeRuntime()

    action = rt.install_guardrail(home, _body(tmp_path), HOOKS)

    entries = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert [e["matcher"] for e in entries] == ["Bash|PowerShell", "*"]
    assert entries[0]["hooks"] == [{"type": "command", "command": command, "timeout": 10}]
    assert entries[1]["hooks"][0]["command"] == "node someone-elses.mjs"
    assert "spec updated" in action
    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None  # and it settles


def test_a_group_that_already_matches_every_tool_is_not_narrowed(tmp_path):
    """A match-all group already reaches the PowerShell tool: rewriting it would lose tools."""
    home, settings = _claude_home(tmp_path)
    command = _registered_command(home)
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 10}]},
    ]}}), encoding="utf-8")
    rt = ClaudeRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)  # deploys the files, leaves the group alone

    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None  # nothing left to repair

    assert json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["matcher"] == "*"


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


def test_a_hook_registered_twice_is_merged_into_one_group(tmp_path):
    """Two groups carrying our command run the adapter twice per Bash command.

    That state is what a machine fixed by hand from the docs produces: an older engine's `Bash`
    group plus a hand-added `Bash|PowerShell` one. The repaired group has to be the only one.
    """
    home, settings = _claude_home(tmp_path)
    command = _registered_command(home)
    other = {"type": "command", "command": "node someone-elses.mjs"}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": 10}]},
        {"matcher": "Bash|PowerShell", "hooks": [{"type": "command", "command": command, "timeout": 10}]},
        {"matcher": "*", "hooks": [other]},
    ]}}), encoding="utf-8")
    rt = ClaudeRuntime()

    action = rt.install_guardrail(home, _body(tmp_path), HOOKS)

    entries = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert sum(1 for e in entries for h in e["hooks"] if h["command"] == command) == 1
    assert entries[0]["matcher"] == "Bash|PowerShell"
    assert entries[-1]["hooks"] == [other]
    assert "spec updated" in action
    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None  # and it settles


def test_a_duplicate_group_that_also_held_another_hook_keeps_that_hook(tmp_path):
    """Another tool's handler in the same group is not ours to remove."""
    home, settings = _claude_home(tmp_path)
    command = _registered_command(home)
    other = {"type": "command", "command": "node someone-elses.mjs"}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash|PowerShell", "hooks": [{"type": "command", "command": command, "timeout": 10}]},
        {"matcher": "Bash|PowerShell", "hooks": [
            {"type": "command", "command": command, "timeout": 10},
            other,
        ]},
    ]}}), encoding="utf-8")

    ClaudeRuntime().install_guardrail(home, _body(tmp_path), HOOKS)

    entries = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert [e["hooks"] for e in entries] == [
        [{"type": "command", "command": command, "timeout": 10}],
        [other],
    ]


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


def _install_and_mediate(tmp_path, config=None):
    home, path = _opencode_home(tmp_path, config)
    rt = OpenCodeRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    rules = {(r["action"], r["resource"]): r["effect"] for r in json.loads(path.read_text(encoding="utf-8"))["permissions"]}
    return home, path, rt, rules


def test_with_the_plugin_installed_the_shell_rule_asks_and_the_plugin_answers(tmp_path):
    """permission.ask is only called for actions OpenCode is about to ask about: a shell rule
    saying allow left the plugin registered and unreachable, and nothing said so."""
    home, _path, rt, rules = _install_and_mediate(tmp_path)

    assert rules[("shell", "*")] == "ask"
    assert ("edit", "*") not in rules  # the edit mode is the app's own setting
    sidecar = rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))
    assert sidecar["autoAllow"] is True and sidecar["strict"] is True


def test_a_v1_binary_keeps_the_config_it_cannot_parse_untouched(tmp_path, opencode_dialect):
    """A V1 CLI refuses the whole config on ordered `permissions`: writing them stops OpenCode."""
    opencode_dialect("1.19.3")
    home, path = _opencode_home(tmp_path)
    before = path.read_text(encoding="utf-8")

    OpenCodeRuntime().install_guardrail(home, _body(tmp_path), HOOKS)

    assert path.read_text(encoding="utf-8") == before


def test_an_allow_left_by_an_earlier_engine_is_converted_not_left_unreachable(tmp_path):
    home, path = _opencode_home(tmp_path, {
        "permissions": [{"action": "shell", "resource": "*", "effect": "allow"}],
    })
    rt = OpenCodeRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)

    rules = {(r["action"], r["resource"]): r["effect"] for r in json.loads(path.read_text(encoding="utf-8"))["permissions"]}
    assert rules[("shell", "*")] == "ask"
    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None  # and it settles


def test_an_explicit_user_deny_is_never_touched(tmp_path):
    _home, _path, _rt, rules = _install_and_mediate(
        tmp_path, {"permissions": [{"action": "shell", "resource": "*", "effect": "deny"}]})
    assert rules[("shell", "*")] == "deny"


def test_reinstalling_the_guardrail_keeps_the_flags_that_let_the_plugin_answer(tmp_path):
    """The flags are set after the sidecar is written; the next guardrail install must not
    undo them, or every command would prompt again."""
    home, _path, rt, _rules = _install_and_mediate(tmp_path)
    rt.install_guardrail(home, _body(tmp_path, "// a newer policy\n"), HOOKS)
    sidecar = rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))
    assert sidecar["autoAllow"] is True and sidecar["strict"] is True


def test_a_shell_rule_the_person_set_to_ask_keeps_prompting(tmp_path):
    """`ask` is what a person picks when they want to approve each command.

    The engine writes the same `ask` rule so the plugin is reachable, and the plugin answers
    what the person is asked: with `autoAllow` on it would answer `allow` for every command the
    body permits, which silently removes the prompt they chose. The rule is left alone and the
    flag stays off, so the plugin only vetoes a denied command.
    """
    home, _path, rt, rules = _install_and_mediate(
        tmp_path, {"permissions": [{"action": "shell", "resource": "*", "effect": "ask"}]})

    assert rules[("shell", "*")] == "ask"
    sidecar = rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))
    assert sidecar["autoAllow"] is False
    # The guardrail is still the brake: `strict` is what blocks a guardrail that cannot answer.
    assert sidecar["strict"] is True
    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None  # and it settles


def test_a_rule_the_engine_itself_mediated_is_not_read_as_the_persons_ask(tmp_path):
    """Second cycle: the `ask` rule on disk is the engine's, and the sidecar says so.

    Reading the rule alone would turn every machine that ever installed the guardrail into a
    prompt-per-command machine on the next cycle thirty minutes later.
    """
    home, _path, rt, _rules = _install_and_mediate(tmp_path)

    rt.install_guardrail(home, _body(tmp_path), HOOKS)

    sidecar = rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))
    assert sidecar["autoAllow"] is True


# ------------------------------------------------------------------ Antigravity


def test_antigravity_registers_the_adapter_so_a_path_with_spaces_still_runs(tmp_path):
    """La home di Windows contiene spesso spazi: il comando non deve contenerla.

    `node C:\\Users\\Mario Rossi\\...\\adapter.mjs` si spezza in due argomenti e
    l'hook del guardrail non parte. Le virgolette non bastano: agy le passa a node
    così come sono. agy esegue l'hook nella cartella di hooks.json, dove sta
    l'adapter, quindi basta il suo nome.
    """
    from nexgen_core.paths import antigravity_settings

    home = tmp_path / "home con spazi"
    home.mkdir()
    settings = antigravity_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")
    rt = AntigravityRuntime()

    rt.install_guardrail(home, _body(tmp_path), HOOKS)

    hooks = json.loads((home / ".gemini" / "config" / "hooks.json").read_text(encoding="utf-8"))
    command = hooks["nexgen-guardrail"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == "node antigravity-guardrail-adapter.mjs"
    assert "con spazi" not in command
    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None  # e si assesta


def test_antigravity_registers_relative_commands_without_quotes_or_spaces(tmp_path):
    """Antigravity runs hooks with CWD set to the hooks.json directory.

    Quoting the command breaks on Windows because cmd /c passes literal quotes
    to node, failing with 'Cannot find module'. Using an unquoted absolute path
    breaks when the home directory contains spaces. A relative command without
    quotes solves both.
    """
    from nexgen_core.paths import antigravity_settings

    home = tmp_path / "home con spazi"
    home.mkdir()
    settings = antigravity_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")
    rt = AntigravityRuntime()

    # Guardrail: relative command without quotes or absolute paths
    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    hooks = json.loads((home / ".gemini" / "config" / "hooks.json").read_text(encoding="utf-8"))
    guardrail_cmd = hooks["nexgen-guardrail"]["PreToolUse"][0]["hooks"][0]["command"]

    assert '"' not in guardrail_cmd
    assert str(home) not in guardrail_cmd
    assert not Path(guardrail_cmd.split()[1]).is_absolute()
    assert guardrail_cmd == "node antigravity-guardrail-adapter.mjs"
    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None


def test_the_antigravity_app_without_the_cli_still_gets_the_global_hook(tmp_path):
    """The App reads the same global `~/.gemini/config/hooks.json` as `agy`.

    With only the app installed there is no `~/.gemini/antigravity-cli/settings.json`,
    and the guardrail used to be skipped: the app's shell commands ran unchecked and
    `nexgen doctor` said nothing, because it skips CLIs that look uninstalled.
    """
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity" / "brain").mkdir(parents=True)
    rt = AntigravityRuntime()

    action = rt.install_guardrail(home, _body(tmp_path), HOOKS)

    assert action is not None and "registered" in action
    hooks = json.loads((home / ".gemini" / "config" / "hooks.json").read_text(encoding="utf-8"))
    assert hooks["nexgen-guardrail"]["PreToolUse"][0]["matcher"] == "run_command"
    assert rt.guardrail_sidecar(home).is_file()


def test_a_fresh_antigravity_install_denies_when_the_guardrail_cannot_answer(tmp_path):
    """`always-proceed` skips an `ask`, so a broken guardrail that asks brakes nothing.

    Nothing in the adapter observes a mode any more, so the closed answer is the only safe
    default; it used to come from the deleted posture step and was `false` here.
    """
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity" / "brain").mkdir(parents=True)
    rt = AntigravityRuntime()

    rt.install_guardrail(home, _body(tmp_path), HOOKS)

    assert rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))["strict"] is True


def test_the_doctor_reports_the_antigravity_app_guardrail_it_can_now_see(tmp_path):
    """The check reads `is_installed`: an app-only machine was silently out of its scope."""
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    (home / ".gemini" / "antigravity" / "installation_id").write_text("x", encoding="utf-8")
    rt = AntigravityRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    _audit(Path(rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))["auditFile"]), count=7)

    (outcome,) = check_guardrail_consulted(home)

    assert outcome.id == "guardrail.consulted.antigravity"
    assert outcome.severity == Severity.OK and "7" in outcome.message


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


# ------------------------------------------------------------------ a retired posture


def _permissions_vault(tmp_path: Path, body: str) -> Path:
    """A vault whose private permission manifest holds exactly `body`."""
    vault = tmp_path / "vault"
    permissions = vault / "03-INFRA" / "agent-universal-layer" / "permissions"
    permissions.mkdir(parents=True)
    (permissions / "manifest.yaml").write_text(body, encoding="utf-8")
    return vault


def test_a_manifest_that_still_declares_a_posture_gets_one_info_line(tmp_path):
    """The key is no longer applied: the doctor says so once, and never fails on it."""
    import nexgen_core.i18n as i18n

    i18n.set_language("en")
    try:
        vault = _permissions_vault(tmp_path, "posture:\n  claude: bypass\nhooks: []\n")

        outcome = check_permission_posture(vault)
    finally:
        i18n.set_language(None)

    assert outcome is not None
    assert outcome.id == "permissions.posture" and outcome.severity == Severity.OK
    assert outcome.message == (
        "posture is no longer applied: each app's own setting decides, except OpenClaw, whose "
        "plugin reads 'posture' in its own nexgen.config.json and behaves as bypass without it"
    )
    assert outcome.action is None


@pytest.mark.parametrize("body", ["posture: bypass\n", "posture: [bypass]\n", "posture: {}\n"])
def test_a_posture_of_any_shape_is_read_without_failing(tmp_path, body):
    outcome = check_permission_posture(_permissions_vault(tmp_path, body))

    assert outcome is not None and outcome.severity == Severity.OK


def test_a_manifest_without_a_posture_says_nothing(tmp_path):
    assert check_permission_posture(_permissions_vault(tmp_path, "hooks: []\n")) is None


def test_a_machine_without_the_private_manifest_says_nothing(tmp_path):
    assert check_permission_posture(tmp_path / "vault") is None


# ------------------------------------------------------------------ Codex: the registration and its trust


#: What a person wrote in `~/.codex/hooks.json` here: hooks of their own, in the same file the
#: engine now registers into. None of them may move.
PERSONS_CODEX_HOOKS = {
    "hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "node someone-elses.mjs", "timeout": 5}]}],
        "PreToolUse": [{"matcher": "apply_patch", "hooks": [{"type": "command", "command": "node patch-check.mjs"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "node stop.mjs", "timeout": 30}]}],
    }
}


@pytest.fixture
def codex_installed(monkeypatch):
    """A machine where the Codex binary exists; the autouse fixture hides every binary."""
    monkeypatch.setattr("nexgen_core.runtimes.codex.shutil.which", lambda name: f"/fake/{name}")


def _codex_home(tmp_path: Path, hooks: dict | None = None, config: str = "") -> tuple[Path, Path, Path]:
    """A Codex home with the hooks a person already had and its own config.toml."""
    home = tmp_path / "home"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    hooks_path = codex / "hooks.json"
    if hooks is not None:
        hooks_path.write_text(json.dumps(hooks, indent=2), encoding="utf-8")
    config_path = codex / "config.toml"
    config_path.write_text(config, encoding="utf-8")
    return home, hooks_path, config_path


def _codex_entries(hooks_path: Path) -> list[dict]:
    return json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]


def _our_codex_index(hooks_path: Path) -> int:
    return next(
        index for index, group in enumerate(_codex_entries(hooks_path))
        if any("codex-guardrail-adapter.mjs" in handler.get("command", "") for handler in group["hooks"])
    )


def _codex_trusts(config_path: Path, hooks_path: Path, index: int, value: str) -> None:
    """Exactly what Codex writes into its own config.toml when a person accepts a hook in /hooks."""
    config_path.write_text(
        "[hooks.state]\n\n"
        f"[hooks.state.'{hooks_path}:pre_tool_use:{index}:0']\n"
        f'trusted_hash = "{value}"\n',
        encoding="utf-8",
    )


def test_codex_registers_our_hook_and_leaves_every_hook_a_person_wrote_alone(tmp_path):
    home, hooks_path, _config = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    rt = CodexRuntime()

    action = rt.install_guardrail(home, _body(tmp_path), HOOKS)

    entries = _codex_entries(hooks_path)
    assert entries[0] == PERSONS_CODEX_HOOKS["hooks"]["PreToolUse"][0]  # untouched, and still first
    assert entries[1] == {
        "matcher": "Bash",
        "hooks": [{"type": "command",
                   "command": f'node "{home / ".codex" / "codex-guardrail-adapter.mjs"}"',
                   "timeout": 10}],
    }
    written = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]
    assert written["UserPromptSubmit"] == PERSONS_CODEX_HOOKS["hooks"]["UserPromptSubmit"]
    assert written["Stop"] == PERSONS_CODEX_HOOKS["hooks"]["Stop"]
    assert "/hooks" in action and "trust" in action  # the person is told what Codex will demand
    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None  # and it settles


def test_codex_gets_a_hook_file_when_it_has_none_yet(tmp_path):
    """hooks.json is created by hand, not by Codex: a machine with the CLI and no file has no hooks at all."""
    home, hooks_path, _config = _codex_home(tmp_path)
    assert not hooks_path.exists()

    action = CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)

    assert "registered" in action
    assert _codex_entries(hooks_path)[0]["matcher"] == "Bash"


def test_the_codex_hook_is_found_by_its_command_not_by_its_position(tmp_path):
    """A group index says nothing about who wrote what: the same hook twice would run twice."""
    home, hooks_path, _config = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)
    entries = _codex_entries(hooks_path)
    entries[1]["hooks"][0]["timeout"] = 2  # an older engine's registration, or a hand edit
    hooks_path.write_text(json.dumps({"hooks": {"PreToolUse": entries}}), encoding="utf-8")

    action = CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)

    entries = _codex_entries(hooks_path)
    assert len(entries) == 2 and entries[1]["hooks"][0]["timeout"] == 10
    assert entries[0] == PERSONS_CODEX_HOOKS["hooks"]["PreToolUse"][0]
    assert "updated" in action


def test_the_codex_sidecar_is_strict_and_its_body_answers_before_codex_gives_up(tmp_path):
    """Codex lets a tool run when a hook fails: the closed answer is the only one that brakes."""
    home, _hooks, _config = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    rt = CodexRuntime()

    rt.install_guardrail(home, _body(tmp_path), HOOKS)

    sidecar = rt.read_guardrail_sidecar(rt.guardrail_sidecar(home))
    assert sidecar["strict"] is True
    assert sidecar["hooks"][0]["timeout"] == 5  # the hook is given 10 s, so the adapter always answers


def test_a_missing_core_is_refused_before_codex_registers_anything(tmp_path):
    home, hooks_path, _config = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    hooks = tmp_path / "hooks"
    shutil.copytree(HOOKS, hooks)
    (hooks / "nexgen-guardrail-core.mjs").unlink()
    before = hooks_path.read_text(encoding="utf-8")

    with pytest.raises(GuardrailError, match="core"):
        CodexRuntime().install_guardrail(home, _body(tmp_path), hooks)

    assert hooks_path.read_text(encoding="utf-8") == before


def test_a_codex_hook_file_that_is_not_json_is_refused_not_replaced(tmp_path):
    home, hooks_path, _config = _codex_home(tmp_path)
    hooks_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(GuardrailError, match="not valid JSON"):
        CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)

    assert hooks_path.read_text(encoding="utf-8") == "{not json"


def test_a_registered_hook_codex_never_accepted_is_not_trusted(tmp_path):
    home, _hooks_path, _config = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)

    CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)

    assert guardrail_trust(home) == UNTRUSTED


def test_a_hash_a_removed_hook_left_at_the_same_slot_is_not_an_acceptance(tmp_path):
    """This machine's config.toml still carries one for a hook that is no longer in the file.

    Trust is a hash Codex compares with the hook it has, so a leftover says nothing about the hook
    the engine just wrote: reading presence as consent is the fake this test exists to refuse.
    """
    home, hooks_path, config_path = _codex_home(tmp_path)
    _codex_trusts(config_path, hooks_path, 0, "sha256:left-by-a-hook-that-is-gone")

    CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)

    assert _our_codex_index(hooks_path) == 0  # the leftover is at exactly our slot
    assert guardrail_trust(home) == UNTRUSTED


def test_the_acceptance_codex_records_after_the_person_opens_hooks_is_seen(tmp_path):
    home, hooks_path, config_path = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)

    _codex_trusts(config_path, hooks_path, _our_codex_index(hooks_path), "sha256:accepted")

    assert guardrail_trust(home) == TRUSTED


def test_a_later_guard_cycle_does_not_lose_the_acceptance(tmp_path):
    """The engine re-installs every few minutes: adopting the accepted hash as its baseline would
    turn a hook the person accepted into an unaccepted one on the next cycle."""
    home, hooks_path, config_path = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    rt = CodexRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    _codex_trusts(config_path, hooks_path, _our_codex_index(hooks_path), "sha256:accepted")

    assert rt.install_guardrail(home, _body(tmp_path), HOOKS) is None

    assert guardrail_trust(home) == TRUSTED


def test_a_changed_declaration_needs_a_fresh_acceptance(tmp_path, monkeypatch):
    """Codex hashes the hook definition: what it accepted is not what is on disk any more."""
    home, hooks_path, config_path = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    rt = CodexRuntime()
    rt.install_guardrail(home, _body(tmp_path), HOOKS)
    _codex_trusts(config_path, hooks_path, _our_codex_index(hooks_path), "sha256:first")
    assert guardrail_trust(home) == TRUSTED

    monkeypatch.setattr("nexgen_core.runtimes.codex._HOOK_TIMEOUT_SECONDS", 30)
    rt.install_guardrail(home, _body(tmp_path), HOOKS)

    assert guardrail_trust(home) == STALE
    _codex_trusts(config_path, hooks_path, _our_codex_index(hooks_path), "sha256:second")
    assert guardrail_trust(home) == TRUSTED


def test_an_edited_hook_is_reported_as_needing_a_review_again(tmp_path):
    """A hand edit to the declaration changes what Codex hashes, with no cycle of ours in between."""
    home, hooks_path, config_path = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)
    _codex_trusts(config_path, hooks_path, _our_codex_index(hooks_path), "sha256:accepted")
    assert guardrail_trust(home) == TRUSTED

    entries = _codex_entries(hooks_path)
    entries[_our_codex_index(hooks_path)]["hooks"][0]["statusMessage"] = "Edited by hand"
    hooks_path.write_text(json.dumps({"hooks": {"PreToolUse": entries}}), encoding="utf-8")

    assert guardrail_trust(home) == STALE

def test_the_doctor_says_the_codex_guardrail_is_untrusted_and_what_to_do(tmp_path, codex_installed):
    import nexgen_core.i18n as i18n

    home, _hooks_path, _config = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)
    i18n.set_language("en")
    try:
        outcome = check_codex_guardrail_trust(home)
    finally:
        i18n.set_language(None)

    assert outcome.id == "guardrail.codex_trust" and outcome.severity == Severity.WARN
    assert outcome.message == "Codex: the guardrail hook is registered but not trusted, so Codex skips it."
    assert outcome.action == "Open Codex, run /hooks and accept the NeXgen guardrail hook."


def test_the_doctor_says_the_codex_guardrail_was_accepted(tmp_path, codex_installed):
    import nexgen_core.i18n as i18n

    home, hooks_path, config_path = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)
    _codex_trusts(config_path, hooks_path, _our_codex_index(hooks_path), "sha256:accepted")
    i18n.set_language("en")
    try:
        outcome = check_codex_guardrail_trust(home)
    finally:
        i18n.set_language(None)

    assert outcome.severity == Severity.OK
    assert outcome.message == "Codex has accepted the guardrail hook, so Codex runs it."


def test_the_doctor_reports_a_guardrail_codex_would_not_read(tmp_path, codex_installed):
    import nexgen_core.i18n as i18n

    home, hooks_path, _config = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)
    hooks_path.write_text(json.dumps({"hooks": {"PreToolUse": []}}), encoding="utf-8")  # an engine's write, undone
    i18n.set_language("en")
    try:
        outcome = check_codex_guardrail_trust(home)
    finally:
        i18n.set_language(None)

    assert outcome.severity == Severity.WARN
    assert outcome.message == "Codex: the guardrail is deployed but not registered in its own hooks.json."


def test_the_doctor_says_nothing_about_codex_without_its_guardrail(tmp_path, codex_installed):
    home, _hooks_path, _config = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)

    assert check_codex_guardrail_trust(home) is None
    assert guardrail_trust(home) is None


def test_the_doctor_reports_a_codex_hook_file_it_cannot_read(tmp_path, codex_installed):
    import nexgen_core.i18n as i18n

    home, hooks_path, _config = _codex_home(tmp_path, PERSONS_CODEX_HOOKS)
    CodexRuntime().install_guardrail(home, _body(tmp_path), HOOKS)
    hooks_path.write_text("{not json", encoding="utf-8")
    i18n.set_language("en")
    try:
        outcome = check_codex_guardrail_trust(home)
    finally:
        i18n.set_language(None)

    assert outcome.severity == Severity.WARN
    assert outcome.message.startswith("Codex: its own hook file could not be read, so the guardrail's trust is unknown")

