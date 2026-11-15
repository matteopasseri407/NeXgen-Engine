"""Unit test per nexgen_core.runtimes: adattatori CLI (hook del guardrail).

Ogni test punta `home`/`vault` su tmp_path -- MAI sulla home reale. La
macchina di sviluppo ha claude/codex/opencode/agy realmente sul PATH, quindi
ogni test che riguarda is_installed() deve azzerare `shutil.which` in modo
esplicito: altrimenti il risultato dipenderebbe da cosa capita di essere
installato su chi esegue i test, non dalla logica sotto test.
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

from nexgen_core.guard import GuardRunner
from nexgen_core.runtimes import REGISTRY, apply_all
from nexgen_core.runtimes.antigravity import AntigravityRuntime
from nexgen_core.runtimes.base import GuardrailError
from nexgen_core.runtimes.claude import ClaudeRuntime
from nexgen_core.runtimes.codex import CodexRuntime
from nexgen_core.runtimes.opencode import OpenCodeRuntime

REAL_HOOKS = Path(__file__).resolve().parents[1] / "hooks"


def _real_hooks(tmp_path: Path) -> Path:
    """A copy of the hook files that ship, standing in for the engine's hooks folder."""
    target = tmp_path / "engine-hooks"
    shutil.copytree(REAL_HOOKS, target)
    return target


@pytest.fixture(autouse=True)
def _no_ambient_binaries(monkeypatch):
    """Azzera `shutil.which` in ogni modulo adattatore: nessun test deve
    dipendere da cosa e' realmente installato sulla macchina che li esegue."""
    for mod in ("claude", "codex", "opencode", "antigravity"):
        monkeypatch.setattr(f"nexgen_core.runtimes.{mod}.shutil.which", lambda name: None)


# ── Claude ───────────────────────────────────────────────────────────────

def test_claude_install_guardrail_registers_hook_and_preserves_other_events(tmp_path: Path):
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    settings = claude_dir / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "node other.mjs"}]}]},
    }), encoding="utf-8")

    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy body\n", encoding="utf-8")

    engine_hooks = _real_hooks(tmp_path)
    rt = ClaudeRuntime()
    action = rt.install_guardrail(home, hook_source, engine_hooks)
    assert action is not None
    body_dst = claude_dir / "nexgen-guardrail-hooks" / "guardrail-catastrophic.mjs"
    assert body_dst.read_text(encoding="utf-8") == "// policy body\n"

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["hooks"]["SessionStart"]  # evento estraneo sopravvive intatto
    pretool = data["hooks"]["PreToolUse"]
    assert pretool[0]["matcher"] == "Bash|PowerShell"
    # The hook is the adapter, which fails closed; the body is what the adapter runs.
    assert pretool[0]["hooks"][0]["command"] == f'node "{claude_dir / "claude-guardrail-adapter.mjs"}"'
    assert pretool[0]["hooks"][0]["timeout"] == 10
    sidecar = json.loads((claude_dir / "nexgen-guardrail.config.json").read_text(encoding="utf-8"))
    assert sidecar["hooks"] == [{"file": str(body_dst), "timeout": 5}] and sidecar["auditFile"].endswith("claude.json")
    assert (claude_dir / "nexgen-guardrail-core.mjs").is_file()

    # Idempotenza: una seconda chiamata non aggiunge una seconda voce.
    again = rt.install_guardrail(home, hook_source, engine_hooks)
    assert again is None
    data2 = json.loads(settings.read_text(encoding="utf-8"))
    assert len(data2["hooks"]["PreToolUse"]) == 1


def test_claude_malformed_settings_refuses_without_writing(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"hooks": "not-an-object"})
    settings.write_text(original, encoding="utf-8")

    rt = ClaudeRuntime()
    with pytest.raises(GuardrailError):
        rt.install_guardrail(home, tmp_path / "irrelevant.mjs", tmp_path)
    # Nessuna scrittura ne' backup per una config che non si puo' modificare in sicurezza.
    assert settings.read_text(encoding="utf-8") == original
    assert not list(home.joinpath(".claude").glob("*.bak"))


def test_claude_is_installed_regression_directory_footprint(tmp_path: Path):
    """~/.claude/skills e' creato dal materializzatore di skill di QUESTO
    layer, indipendentemente da Claude essere installato. Solo settings.json
    (scritto solo da Claude stesso) o il binario contano."""
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    assert ClaudeRuntime().is_installed(home) is False


# ── Codex ────────────────────────────────────────────────────────────────

CODEX_TOML = '''# commento dell'utente
model = "o4"
approval_policy = "on-request"
sandbox_mode = "workspace-write"

[profile.dev]
foo = "bar"
'''


def test_codex_no_guardrail_ever(tmp_path: Path):
    home = tmp_path / "home"
    assert CodexRuntime().install_guardrail(home, tmp_path / "x.mjs", tmp_path) is None


def test_codex_is_installed_regression_config_written_by_own_renderer(tmp_path: Path):
    """config.toml e' scritto anche dal renderer MCP di questo stesso layer
    ad ogni ciclo, installato Codex o no: la sua sola esistenza non basta."""
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("model = \"o4\"\n", encoding="utf-8")
    assert CodexRuntime().is_installed(home) is False


# ── OpenCode ─────────────────────────────────────────────────────────────

def _write_opencode_config(home: Path) -> Path:
    path = home / ".config" / "opencode" / "opencode.jsonc"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "permission": {"webfetch": "ask"},
        "plugin": ["some-other-plugin"],
    }, indent=2), encoding="utf-8")
    return path


def _policy_body(tmp_path: Path) -> Path:
    """Stand-in for the private guardrail body the manifest declares."""
    body = tmp_path / "guardrail-catastrophic.mjs"
    body.write_text("// policy\n", encoding="utf-8")
    return body


def test_opencode_the_guardrail_sets_the_mediated_shell_rule(tmp_path: Path, opencode_dialect):
    """V2 native shape: the legacy `permission` object migrates once into ordered
    `permissions` rules, the shell rule says `ask` so the plugin is consulted, and the
    person's own plugins are untouched."""
    opencode_dialect("2.2.0")
    home = tmp_path / "home"
    config = _write_opencode_config(home)
    engine_hooks = _engine_hooks(tmp_path)

    action = OpenCodeRuntime().install_guardrail(home, _policy_body(tmp_path), engine_hooks)
    assert action is not None

    data = json.loads(config.read_text(encoding="utf-8"))
    assert "permission" not in data  # migrated, not duplicated
    rules = {(r["action"], r["resource"]): r["effect"] for r in data["permissions"]}
    assert rules[("shell", "*")] == "ask"  # V1 `bash` is V2 `shell`
    assert rules[("webfetch", "*")] == "ask"  # dimensione estranea sopravvive
    assert ("edit", "*") not in rules  # the edit mode is the app's own setting
    assert data["plugin"] == ["some-other-plugin"]  # plugin dell'utente intatti

    sidecar = OpenCodeRuntime().read_guardrail_sidecar(OpenCodeRuntime().guardrail_sidecar(home))
    assert sidecar["autoAllow"] is True and sidecar["strict"] is True

    # Idempotenza: la seconda corsa non riscrive niente.
    assert OpenCodeRuntime().install_guardrail(home, _policy_body(tmp_path), engine_hooks) is None


def test_opencode_the_shell_rule_never_overrides_an_explicit_user_deny(tmp_path: Path, opencode_dialect):
    """Ordered rules: a user deny on the same pair wins. Upgrading it to the engine's `ask`
    would punch a hole in a fence the user built on purpose."""
    opencode_dialect("2.2.0")
    home = tmp_path / "home"
    config = home / ".config" / "opencode" / "opencode.jsonc"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "permissions": [{"action": "shell", "resource": "*", "effect": "deny"}],
    }, indent=2), encoding="utf-8")

    OpenCodeRuntime().install_guardrail(home, _policy_body(tmp_path), _engine_hooks(tmp_path))

    data = json.loads(config.read_text(encoding="utf-8"))
    shell_rules = [r for r in data["permissions"] if r["action"] == "shell"]
    assert shell_rules == [{"action": "shell", "resource": "*", "effect": "deny"}]


def test_opencode_writes_no_rules_a_v1_binary_would_refuse(tmp_path: Path, opencode_dialect):
    """V1 refuses the whole config on ordered `permissions`, so the writer stays off."""
    opencode_dialect("1.19.3")
    home = tmp_path / "home"
    config = _write_opencode_config(home)
    before = config.read_text(encoding="utf-8")

    OpenCodeRuntime().install_guardrail(home, _policy_body(tmp_path), _engine_hooks(tmp_path))

    assert config.read_text(encoding="utf-8") == before


def _engine_hooks(tmp_path: Path) -> Path:
    engine_hooks_dir = tmp_path / "engine-hooks"
    engine_hooks_dir.mkdir()
    (engine_hooks_dir / "opencode-guardrail-plugin.mjs").write_text("// adapter\n", encoding="utf-8")
    (engine_hooks_dir / "nexgen-guardrail-core.mjs").write_text("// core\n", encoding="utf-8")
    return engine_hooks_dir


def test_opencode_install_guardrail_deploys_a_plugin_directory_and_registers_nothing(tmp_path: Path):
    """OpenCode 2.0.24 loads every directory under `plugins/` by itself and refuses a plugin registered as a file."""
    home = tmp_path / "home"
    config = _write_opencode_config(home)
    before = json.loads(config.read_text(encoding="utf-8"))
    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy\n", encoding="utf-8")
    engine_hooks_dir = _engine_hooks(tmp_path)

    rt = OpenCodeRuntime()
    action = rt.install_guardrail(home, hook_source, engine_hooks_dir)
    assert action is None or "plugin" in action

    data = json.loads(config.read_text(encoding="utf-8"))
    registered = [p for key in ("plugin", "plugins") for p in data.get(key, []) if isinstance(p, str)]
    assert not any("guardrail" in p for p in registered), "nothing is registered by path: that is what OpenCode refused"
    assert data.get("plugin") == before.get("plugin"), "the person's own plugins are left exactly as they were"

    plugin_dir = config.parent / "plugins" / "nexgen-guardrail"
    assert (plugin_dir / "index.mjs").read_text(encoding="utf-8") == "// adapter\n"
    assert (plugin_dir / "nexgen-guardrail-core.mjs").read_text(encoding="utf-8") == "// core\n"
    body_dst = plugin_dir / "hooks" / "guardrail-catastrophic.mjs"
    assert body_dst.read_text(encoding="utf-8") == "// policy\n"
    sidecar = json.loads((plugin_dir / "nexgen-guardrail.config.json").read_text(encoding="utf-8"))
    assert sidecar["hooks"][0]["file"] == str(body_dst)
    assert rt.guardrail_sidecar(home) == plugin_dir / "nexgen-guardrail.config.json"

    # Idempotenza
    assert rt.install_guardrail(home, hook_source, engine_hooks_dir) is None


def test_opencode_install_guardrail_takes_back_what_an_earlier_engine_registered_by_path(tmp_path: Path):
    home = tmp_path / "home"
    config = _write_opencode_config(home)
    config_dir = config.parent
    data = json.loads(config.read_text(encoding="utf-8"))
    data["plugins"] = [
        (config_dir / "opencode-guardrail-plugin.mjs").as_uri(),
        (config_dir / "nexgen-guardrail-plugin.mjs").as_uri(),
        "keep-me",
        (config_dir / "other-plugin.mjs").as_uri(),
    ]
    config.write_text(json.dumps(data), encoding="utf-8")
    for name in ("opencode-guardrail-plugin.mjs", "nexgen-guardrail-plugin.mjs", "nexgen-guardrail-core.mjs", "nexgen-guardrail.config.json"):
        (config_dir / name).write_text("old", encoding="utf-8")
    (config_dir / "nexgen-guardrail-hooks").mkdir()
    (config_dir / "nexgen-guardrail-hooks" / "body.mjs").write_text("old", encoding="utf-8")
    (config_dir / "mine.mjs").write_text("the person's own", encoding="utf-8")
    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy\n", encoding="utf-8")

    action = OpenCodeRuntime().install_guardrail(home, hook_source, _engine_hooks(tmp_path))

    assert action is not None and "never loaded" in action
    plugins = json.loads(config.read_text(encoding="utf-8"))["plugins"]
    assert plugins == ["keep-me", (config_dir / "other-plugin.mjs").as_uri()]
    for name in ("opencode-guardrail-plugin.mjs", "nexgen-guardrail-plugin.mjs", "nexgen-guardrail-core.mjs", "nexgen-guardrail.config.json"):
        assert not (config_dir / name).exists()
    assert not (config_dir / "nexgen-guardrail-hooks").exists()
    assert (config_dir / "mine.mjs").read_text(encoding="utf-8") == "the person's own"
    assert (config_dir / "plugins" / "nexgen-guardrail" / "index.mjs").is_file()


def test_opencode_is_installed_regression_config_written_by_own_renderer(tmp_path: Path):
    home = tmp_path / "home"
    _write_opencode_config(home)
    assert OpenCodeRuntime().is_installed(home) is False


def test_opencode_is_installed_via_own_bin_marker(tmp_path: Path):
    home = tmp_path / "home"
    bin_dir = home / ".opencode" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "opencode").write_text("#!/bin/sh\n", encoding="utf-8")
    assert OpenCodeRuntime().is_installed(home) is True


# ── Antigravity ──────────────────────────────────────────────────────────

def _write_antigravity_settings(home: Path) -> Path:
    path = home / ".gemini" / "antigravity-cli" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"someOtherKey": "keep-me"}), encoding="utf-8")
    return path


def test_antigravity_install_guardrail_preserves_foreign_hooks_json_keys(tmp_path: Path):
    home = tmp_path / "home"
    _write_antigravity_settings(home)
    hooks_path = home / ".gemini" / "config" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(json.dumps({"some-other-tool-hook": {"enabled": True}}), encoding="utf-8")

    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy\n", encoding="utf-8")
    engine_hooks_dir = tmp_path / "engine-hooks"
    engine_hooks_dir.mkdir()
    (engine_hooks_dir / "antigravity-guardrail-adapter.mjs").write_text("// adapter\n", encoding="utf-8")
    (engine_hooks_dir / "nexgen-guardrail-core.mjs").write_text("// core\n", encoding="utf-8")

    rt = AntigravityRuntime()
    action = rt.install_guardrail(home, hook_source, engine_hooks_dir)
    assert action is not None

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert data["some-other-tool-hook"] == {"enabled": True}  # chiave estranea sopravvive
    assert data["nexgen-guardrail"]["PreToolUse"][0]["matcher"] == "run_command"

    backups = list(hooks_path.parent.glob("hooks.json.pre-permissions-*.bak"))
    assert len(backups) == 1

    again = rt.install_guardrail(home, hook_source, engine_hooks_dir)
    assert again is None


def test_antigravity_is_installed_regression_directory_footprint(tmp_path: Path):
    """~/.gemini/antigravity-cli/skills e' creato dal materializzatore di
    skill di questo stesso layer -- settings.json (scritto solo da
    Antigravity) e' l'unico segnale valido oltre al binario."""
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity-cli" / "skills").mkdir(parents=True)
    assert AntigravityRuntime().is_installed(home) is False


@pytest.mark.parametrize("footprint", ["history.jsonl", "conversations", "cli.log"])
def test_antigravity_is_installed_from_the_cli_own_files_without_settings_json(
    tmp_path: Path, footprint: str,
):
    """Una CLI installata e mai avviata non ha settings.json, ma ha già i suoi file."""
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (home / ".gemini" / "antigravity-cli" / footprint).touch()
    assert AntigravityRuntime().is_installed(home) is True


@pytest.mark.parametrize("app_dir", ["antigravity", "antigravity-ide"])
@pytest.mark.parametrize("footprint", ["installation_id", "brain"])
def test_antigravity_is_installed_from_the_app_or_ide_own_state_without_agy(
    tmp_path: Path, app_dir: str, footprint: str,
):
    """L'app e l'IDE leggono lo stesso `~/.gemini/config/hooks.json` della CLI.

    Su una macchina con l'app ma senza `agy` il guardrail non veniva installato e il
    doctor non riportava niente: verde su un file che nessuno sorvegliava.
    """
    home = tmp_path / "home"
    state = home / ".gemini" / app_dir
    state.mkdir(parents=True)
    (state / footprint).touch()
    assert AntigravityRuntime().is_installed(home) is True


def test_antigravity_mcp_config_the_engine_fans_out_is_not_an_install(tmp_path: Path):
    """Il renderer scrive `mcp_config.json` in tutte e tre le cartelle: non prova niente."""
    home = tmp_path / "home"
    for directory in ("antigravity", "antigravity-cli", "antigravity-ide"):
        state = home / ".gemini" / directory
        state.mkdir(parents=True)
        (state / "mcp_config.json").write_text("{}", encoding="utf-8")
    assert AntigravityRuntime().is_installed(home) is False


# ── apply_all: l'integrazione ────────────────────────────────────────────

def test_apply_all_skips_every_cli_when_none_installed(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    actions = apply_all(
        home=home,
        engine_hooks_dir=tmp_path / "engine-hooks",
        guardrail_source=tmp_path / "does-not-matter.mjs",
    )
    assert actions == []
    assert not any(home.iterdir())  # nessun file creato per CLI assenti


def test_apply_all_warns_and_writes_nothing_when_the_guardrail_cannot_install(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"hooks": "not-an-object"})
    settings.write_text(original, encoding="utf-8")

    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy\n", encoding="utf-8")

    actions = apply_all(
        home=home,
        engine_hooks_dir=tmp_path / "engine-hooks",
        guardrail_source=hook_source,
    )
    assert any("[WARN]" in a and "claude" in a for a in actions)
    # A hook that could not be registered leaves the file exactly as it was.
    assert settings.read_text(encoding="utf-8") == original


def test_registry_has_one_adapter_per_supported_cli():
    assert set(REGISTRY) == {"claude", "codex", "opencode", "antigravity", "openclaw"}


def test_apply_all_reports_a_missing_node_even_when_no_action_says_hook(tmp_path: Path, opencode_dialect):
    """OpenCode answers "the plugin answers it, in opencode.json", and that is a node-spawned hook.

    The warning used to fire only when an action line contained the word "hook", so on a machine
    with OpenCode or OpenClaw and no `node` on PATH every registration was dead and nothing said
    so. This suite patches `shutil.which` to None, so `node` is missing here too.
    """
    opencode_dialect("2.2.0")
    home = tmp_path / "home"
    config = home / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    binary = home / ".opencode" / "bin" / "opencode"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")

    actions = apply_all(
        home=home,
        engine_hooks_dir=_real_hooks(tmp_path),
        guardrail_source=_policy_body(tmp_path),
    )

    assert any("shell rule set to 'ask'" in action for action in actions)
    assert any("`node` is not on PATH" in action for action in actions)


# ── GuardRunner.apply_runtime_permissions: il punto di integrazione ───────

def test_guard_apply_runtime_permissions_noop_without_manifest(tmp_path: Path):
    runner = GuardRunner(vault_data=tmp_path / "vault", home=tmp_path / "home")
    assert runner.apply_runtime_permissions() == []


def _permissions_vault(tmp_path: Path, posture_block: str = "posture:\n  claude: bypass\n") -> Path:
    """A vault whose private permissions manifest declares one guardrail body."""
    vault = tmp_path / "vault"
    permissions_dir = vault / "03-INFRA" / "agent-universal-layer" / "permissions"
    (permissions_dir / "hooks").mkdir(parents=True)
    (permissions_dir / "hooks" / "guardrail-catastrophic.mjs").write_text("// policy\n", encoding="utf-8")
    (permissions_dir / "manifest.yaml").write_text(
        posture_block
        + "hooks:\n  - name: guardrail-catastrophic\n"
          "    file: hooks/guardrail-catastrophic.mjs\n    targets: [claude]\n",
        encoding="utf-8",
    )
    return vault


def _engine_root_with_real_hooks(tmp_path: Path) -> Path:
    """An engine root whose hooks folder is a copy of the adapters that ship."""
    engine_root = tmp_path / "engine" / "03-INFRA"
    shutil.copytree(REAL_HOOKS, engine_root / "agent-universal-layer" / "hooks")
    return engine_root


def test_a_guard_cycle_does_not_touch_the_permission_mode_claude_chose(tmp_path: Path):
    """`permissions.defaultMode` is Claude's own setting: a cycle registers the hook and
    leaves the mode the person picked, including after a second cycle."""
    vault = _permissions_vault(tmp_path)
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "permissions": {"defaultMode": "acceptEdits", "allow": ["Bash(ls:*)"]},
    }, indent=2), encoding="utf-8")

    runner = GuardRunner(vault_data=vault, engine_root=_engine_root_with_real_hooks(tmp_path), home=home)
    assert runner.apply_runtime_permissions()

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"]["defaultMode"] == "acceptEdits"
    assert data["permissions"]["allow"] == ["Bash(ls:*)"]
    assert "skipDangerousModePermissionPrompt" not in data
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "Bash|PowerShell"

    runner.apply_runtime_permissions()
    again = json.loads(settings.read_text(encoding="utf-8"))
    assert again["permissions"] == data["permissions"]


def test_a_guard_cycle_does_not_touch_the_codex_keys_the_app_wrote(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("nexgen_core.runtimes.codex.shutil.which", lambda name: "codex")
    vault = _permissions_vault(tmp_path, "posture:\n  codex: bypass\n")
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    original = CODEX_TOML
    config.write_text(original, encoding="utf-8")

    runner = GuardRunner(vault_data=vault, engine_root=_engine_root_with_real_hooks(tmp_path), home=home)
    runner.apply_runtime_permissions()

    assert config.read_text(encoding="utf-8") == original


def test_a_guard_cycle_does_not_touch_the_antigravity_keys_the_app_wrote(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("nexgen_core.runtimes.antigravity.shutil.which", lambda name: "agy")
    vault = _permissions_vault(tmp_path, "posture:\n  antigravity: bypass\n")
    home = tmp_path / "home"
    settings = home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"toolPermission": "ask", "artifactReviewPolicy": "ask"}, indent=2)
    settings.write_text(original, encoding="utf-8")

    runner = GuardRunner(vault_data=vault, engine_root=_engine_root_with_real_hooks(tmp_path), home=home)
    runner.apply_runtime_permissions()

    assert settings.read_text(encoding="utf-8") == original
    hooks = json.loads((home / ".gemini" / "config" / "hooks.json").read_text(encoding="utf-8"))
    assert hooks["nexgen-guardrail"]["PreToolUse"][0]["matcher"] == "run_command"


def test_a_guard_cycle_registers_the_guardrail_in_every_runtime(tmp_path: Path, monkeypatch, opencode_dialect):
    """The brake is what stays: each adapter that writes a hook writes it, and OpenCode keeps
    the `shell: ask` rule plus `autoAllow` that make its plugin reachable."""
    opencode_dialect("2.2.0")
    for module, binary in (("claude", "claude"), ("codex", "codex"), ("opencode", "opencode"), ("antigravity", "agy")):
        monkeypatch.setattr(f"nexgen_core.runtimes.{module}.shutil.which", lambda name, _b=binary: _b)
    vault = _permissions_vault(tmp_path, "")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (home / ".gemini" / "antigravity-cli" / "settings.json").write_text("{}", encoding="utf-8")
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".config" / "opencode" / "opencode.json").write_text("{}", encoding="utf-8")
    (home / ".openclaw").mkdir()
    (home / ".openclaw" / "openclaw.json").write_text('{"mcp": {"servers": {}}}', encoding="utf-8")

    runner = GuardRunner(vault_data=vault, engine_root=_engine_root_with_real_hooks(tmp_path), home=home)
    actions = runner.apply_runtime_permissions()
    assert actions

    claude_hooks = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert any("claude-guardrail-adapter.mjs" in item["command"] for group in claude_hooks for item in group["hooks"])
    assert (home / ".gemini" / "config" / "hooks.json").is_file()
    opencode_plugin = home / ".config" / "opencode" / "plugins" / "nexgen-guardrail"
    assert (opencode_plugin / "index.mjs").is_file()
    opencode = json.loads((home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert {"action": "shell", "resource": "*", "effect": "ask"} in opencode["permissions"]
    opencode_sidecar = json.loads((opencode_plugin / "nexgen-guardrail.config.json").read_text(encoding="utf-8"))
    assert opencode_sidecar["autoAllow"] is True and opencode_sidecar["strict"] is True
    openclaw_plugin = home / ".openclaw" / "extensions" / "nexgen"
    assert (openclaw_plugin / "guardrail.mjs").is_file() and (openclaw_plugin / "index.mjs").is_file()
    assert (openclaw_plugin / "nexgen-guardrail.config.json").is_file()
    # No permission mode is written anywhere: the apps own those keys now.
    assert "posture" not in (openclaw_plugin / "nexgen.config.json").read_text(encoding="utf-8")
    assert "defaultMode" not in (home / ".claude" / "settings.json").read_text(encoding="utf-8")


def test_guard_apply_runtime_permissions_rejects_path_traversal(tmp_path: Path):
    vault = tmp_path / "vault"
    permissions_dir = vault / "03-INFRA" / "agent-universal-layer" / "permissions"
    permissions_dir.mkdir(parents=True)
    outside = tmp_path / "outside.mjs"
    outside.write_text("// not part of the vault\n", encoding="utf-8")
    (permissions_dir / "manifest.yaml").write_text(
        f"posture:\n  claude: bypass\nhooks:\n  - name: evil\n    file: {outside}\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"permissions": {}})
    settings.write_text(original, encoding="utf-8")

    runner = GuardRunner(vault_data=vault, home=home)
    actions = runner.apply_runtime_permissions()
    assert any("WARN" in a for a in actions)
    assert settings.read_text(encoding="utf-8") == original
