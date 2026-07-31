"""Tests for the claude_permissions phase.

The phase carries a private permission posture from the vault into Claude's
settings. The posture it exists to apply is `bypassPermissions`, so a bug here
does not merely fail: it can leave a machine with prompts disabled and no
guardrail. Every test below is written against that failure mode, and the
first one is the most important: with no manifest, nothing happens at all.
"""
from __future__ import annotations

import json

import pytest

from conftest import load_agent_sync_module

GUARDRAIL_BODY = "// test guardrail\nprocess.exit(0);\n"

BASE_SETTINGS = {
    "model": "opus[1m]",
    "theme": "auto",
    "permissions": {"allow": ["Bash(ls)"]},
    "hooks": {
        "SessionStart": [
            {"hooks": [{"type": "command", "command": 'node "/pre/existing.mjs"', "timeout": 5}]}
        ]
    },
}


@pytest.fixture
def agent_sync(sandbox):
    return load_agent_sync_module(sandbox)


@pytest.fixture
def env(sandbox, agent_sync, monkeypatch):
    monkeypatch.setenv("HOME", str(sandbox.home))
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", str(sandbox.vault))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(sandbox.vault))
    (sandbox.home / ".claude").mkdir(parents=True, exist_ok=True)
    return agent_sync.Env()


def _settings(sandbox) -> dict:
    return json.loads((sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8"))


def _write_settings(sandbox, data=None) -> None:
    path = sandbox.home / ".claude" / "settings.json"
    path.write_text(json.dumps(data if data is not None else BASE_SETTINGS, indent=2), encoding="utf-8")


def _write_manifest(sandbox, body: str, *, with_hook_body: bool = True) -> None:
    perms = sandbox.ul / "permissions"
    (perms / "hooks").mkdir(parents=True, exist_ok=True)
    (perms / "manifest.yaml").write_text(body, encoding="utf-8")
    if with_hook_body:
        (perms / "hooks" / "guardrail.mjs").write_text(GUARDRAIL_BODY, encoding="utf-8")


VALID_MANIFEST = (
    "schema_version: 1\n"
    "posture:\n"
    "  claude: bypass\n"
    "hooks:\n"
    "  - name: guardrail\n"
    "    file: hooks/guardrail.mjs\n"
    "    targets: [claude]\n"
    "    event: PreToolUse\n"
    "    matcher: Bash\n"
    "    timeout: 5\n"
)

CODEX_CONFIG_BASE = (
    'model = "fake-model"\n'
    "\n"
    "[mcp_servers.fake_tool]\n"
    'command = "fake-cmd"\n'
)


def _write_codex_config(sandbox, body: str = CODEX_CONFIG_BASE):
    path = sandbox.home / ".codex" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _write_opencode_config(sandbox, data=None):
    path = sandbox.home / ".config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data if data is not None else {"model": "fake-provider/fake-model"}, indent=2),
        encoding="utf-8",
    )
    return path


def _write_antigravity_config(sandbox, data=None):
    path = sandbox.home / ".gemini" / "antigravity-cli" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            data if data is not None else {"toolPermission": "request-review", "permissions": {"allow": ["command(ls)"]}},
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


# ---- the safety net: no policy, no change ---------------------------------

def test_absent_manifest_is_a_complete_no_op(sandbox, agent_sync, env):
    """An end user who installs the public engine has no permissions manifest.
    They must never inherit anyone's posture, so nothing may be written."""
    _write_settings(sandbox)
    before = (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is True

    assert (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8") == before
    assert "defaultMode" not in _settings(sandbox)["permissions"]


# ---- the happy path -------------------------------------------------------

def test_bypass_posture_is_applied_with_its_guardrail(sandbox, agent_sync, env):
    _write_manifest(sandbox, VALID_MANIFEST)
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is True

    body = sandbox.home / ".claude" / "guardrail.mjs"
    assert body.read_text(encoding="utf-8") == GUARDRAIL_BODY

    s = _settings(sandbox)
    assert s["permissions"]["defaultMode"] == "bypassPermissions"
    # Without this Claude stops on an interactive dialog no guard run can answer.
    assert s["skipDangerousModePermissionPrompt"] is True

    entries = s["hooks"]["PreToolUse"]
    assert len(entries) == 1
    assert entries[0]["matcher"] == "Bash"
    assert entries[0]["hooks"][0]["command"] == f'node "{body}"'
    assert entries[0]["hooks"][0]["timeout"] == 5


def test_unrelated_user_settings_survive(sandbox, agent_sync, env):
    """The phase writes two keys. Everything else in settings.json belongs to
    the user, including their own allow list and their other hooks."""
    _write_manifest(sandbox, VALID_MANIFEST)
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is True

    s = _settings(sandbox)
    assert s["model"] == "opus[1m]"
    assert s["theme"] == "auto"
    assert s["permissions"]["allow"] == ["Bash(ls)"]
    assert s["hooks"]["SessionStart"][0]["hooks"][0]["command"] == 'node "/pre/existing.mjs"'


def test_running_twice_changes_nothing(sandbox, agent_sync, env):
    """agent-sync asserts full idempotency: a duplicated hook entry would make
    the guardrail run twice per command and the settings file churn forever."""
    _write_manifest(sandbox, VALID_MANIFEST)
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is True
    after_first = (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is True

    assert (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8") == after_first
    assert len(_settings(sandbox)["hooks"]["PreToolUse"]) == 1


def test_accept_edits_posture_does_not_set_the_dangerous_mode_flag(sandbox, agent_sync, env):
    _write_manifest(sandbox, VALID_MANIFEST.replace("claude: bypass", "claude: accept-edits"))
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is True

    s = _settings(sandbox)
    assert s["permissions"]["defaultMode"] == "acceptEdits"
    assert "skipDangerousModePermissionPrompt" not in s


# ---- refusals: never leave a half-applied posture --------------------------

def test_missing_hook_body_refuses_before_touching_settings(sandbox, agent_sync, env):
    """Registering a hook whose file does not exist would leave Claude calling
    a missing script on every Bash command, with prompts already disabled."""
    _write_manifest(sandbox, VALID_MANIFEST, with_hook_body=False)
    _write_settings(sandbox)
    before = (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is False

    assert (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8") == before


def test_unknown_posture_is_refused(sandbox, agent_sync, env):
    _write_manifest(sandbox, VALID_MANIFEST.replace("claude: bypass", "claude: yolo"))
    _write_settings(sandbox)
    before = (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is False

    assert (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8") == before


def test_unknown_cli_in_posture_warns_and_still_applies_claude(sandbox, agent_sync, env):
    """The 2026-07-30 live incident this generalization fixes: a manifest
    posture entry for a CLI this engine cannot yet render must not refuse the
    whole phase and take Claude's OWN posture down with it. It must warn,
    skip that one entry, and still apply everything it can -- Claude's
    bypass+guardrail included -- returning success."""
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  mystery-cli: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is True

    s = _settings(sandbox)
    assert s["permissions"]["defaultMode"] == "bypassPermissions"

    log = env.log_path.read_text(encoding="utf-8")
    assert "mystery-cli" in log
    assert "no verified renderer" in log
    assert "UNAPPLIED" in log


def test_known_cli_with_unverified_posture_value_warns_and_skips(sandbox, agent_sync, env):
    """Codex only has a verified renderer for 'bypass' (see PERMISSION_RENDERERS
    in config_schema.py); 'accept-edits' has no confirmed real-install default
    and must not be guessed at, even though 'codex' itself IS a known CLI."""
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  codex: accept-edits\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)
    codex_config = _write_codex_config(sandbox)
    before = codex_config.read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is True

    assert codex_config.read_text(encoding="utf-8") == before
    log = env.log_path.read_text(encoding="utf-8")
    assert "codex" in log
    assert "UNAPPLIED" in log


def test_non_claude_bypass_without_a_guardrail_hook_logs_a_loud_warning(sandbox, agent_sync, env):
    """Hooks are wired only for Claude today. Applying bypass for any other
    CLI is still the explicit will of whoever owns the manifest, but running
    with no net must never be silent."""
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  codex: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)
    _write_codex_config(sandbox)

    assert agent_sync.claude_permissions(env) is True

    log = env.log_path.read_text(encoding="utf-8")
    assert "codex" in log
    assert "WITHOUT A NET" in log


def test_hook_file_escaping_the_permissions_dir_is_refused(sandbox, agent_sync, env):
    """The manifest is data; `file` is joined onto the vault and copied into
    the user's runtime dir, so a traversal payload must fail closed."""
    _write_manifest(sandbox, VALID_MANIFEST.replace("hooks/guardrail.mjs", "../../../../evil.mjs"))
    _write_settings(sandbox)
    before = (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is False

    assert (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8") == before
    assert not (sandbox.home / ".claude" / "evil.mjs").exists()


def test_malformed_hooks_block_refuses_without_writing_the_posture(sandbox, agent_sync, env):
    """The regression this phase exists to prevent. An earlier version logged
    'skipping hook merge' and carried on, writing bypassPermissions while the
    guardrail stayed unregistered: prompts off, nothing watching."""
    _write_manifest(sandbox, VALID_MANIFEST)
    _write_settings(sandbox, {**BASE_SETTINGS, "hooks": "not-an-object"})
    before = (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is False

    assert (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8") == before
    assert "defaultMode" not in _settings(sandbox).get("permissions", {})
    assert "skipDangerousModePermissionPrompt" not in _settings(sandbox)


def test_malformed_event_list_refuses_without_writing_the_posture(sandbox, agent_sync, env):
    """Same rule one level down: the event exists but is not a list."""
    _write_manifest(sandbox, VALID_MANIFEST)
    _write_settings(sandbox, {**BASE_SETTINGS, "hooks": {"PreToolUse": "nope"}})
    before = (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is False

    assert (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8") == before
    assert "defaultMode" not in _settings(sandbox).get("permissions", {})


def test_malformed_permissions_block_refuses_and_leaves_no_hook_behind(sandbox, agent_sync, env):
    """The mirror case: hooks are fine but `permissions` is not an object.
    Refusing must also discard the hook registration, so the file never lands
    in a state neither the user nor the manifest asked for."""
    _write_manifest(sandbox, VALID_MANIFEST)
    _write_settings(sandbox, {**BASE_SETTINGS, "permissions": "not-an-object"})
    before = (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is False

    assert (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8") == before
    assert "PreToolUse" not in _settings(sandbox).get("hooks", {})


def test_malformed_settings_json_is_refused(sandbox, agent_sync, env):
    _write_manifest(sandbox, VALID_MANIFEST)
    (sandbox.home / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")

    assert agent_sync.claude_permissions(env) is False


def test_wrong_schema_version_is_refused(sandbox, agent_sync, env):
    _write_manifest(sandbox, VALID_MANIFEST.replace("schema_version: 1", "schema_version: 99"))
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is False


def test_a_backup_is_kept_before_the_first_write(sandbox, agent_sync, env):
    _write_manifest(sandbox, VALID_MANIFEST)
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is True

    backups = list((sandbox.home / ".claude").glob("settings.json.pre-permissions-*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == BASE_SETTINGS


# ---- Codex renderer: approval_policy / sandbox_mode, root-level TOML ------

def test_codex_bypass_posture_is_applied(sandbox, agent_sync, env):
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  codex: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)
    path = _write_codex_config(sandbox)

    assert agent_sync.claude_permissions(env) is True

    text = path.read_text(encoding="utf-8")
    assert 'approval_policy = "never"' in text
    assert 'sandbox_mode = "danger-full-access"' in text
    # Surgical: everything else in the file (model, the mcp_servers table)
    # survives untouched.
    assert 'model = "fake-model"' in text
    assert "[mcp_servers.fake_tool]" in text
    assert 'command = "fake-cmd"' in text


def test_codex_bypass_posture_is_idempotent(sandbox, agent_sync, env):
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  codex: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)
    path = _write_codex_config(sandbox)

    assert agent_sync.claude_permissions(env) is True
    after_first = path.read_text(encoding="utf-8")
    backups = list((sandbox.home / ".codex").glob("config.toml.pre-permissions-*.bak"))
    assert len(backups) == 1

    assert agent_sync.claude_permissions(env) is True

    assert path.read_text(encoding="utf-8") == after_first
    backups = list((sandbox.home / ".codex").glob("config.toml.pre-permissions-*.bak"))
    assert len(backups) == 1  # unchanged: nothing was rewritten the 2nd time


def test_codex_config_absent_is_a_no_op(sandbox, agent_sync, env):
    """Codex was never launched on this host: nothing to touch, and the
    engine must not fabricate a config file for a CLI that isn't there."""
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  codex: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is True

    assert not (sandbox.home / ".codex").exists()


# ---- OpenCode renderer: permission.edit / permission.bash -----------------

def test_opencode_bypass_posture_is_applied(sandbox, agent_sync, env):
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  opencode: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)
    path = _write_opencode_config(sandbox)

    assert agent_sync.claude_permissions(env) is True

    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["permission"] == {"edit": "allow", "bash": "allow"}
    assert config["model"] == "fake-provider/fake-model"


def test_opencode_accept_edits_posture_is_applied(sandbox, agent_sync, env):
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  opencode: accept-edits\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)
    path = _write_opencode_config(sandbox)

    assert agent_sync.claude_permissions(env) is True

    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["permission"] == {"edit": "allow", "bash": "ask"}


def test_opencode_posture_is_idempotent_and_preserves_other_permission_keys(sandbox, agent_sync, env):
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  opencode: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)
    path = _write_opencode_config(sandbox, {
        "model": "fake-provider/fake-model",
        "permission": {"webfetch": "ask"},
    })

    assert agent_sync.claude_permissions(env) is True
    after_first = path.read_text(encoding="utf-8")
    config = json.loads(after_first)
    assert config["permission"] == {"webfetch": "ask", "edit": "allow", "bash": "allow"}
    backups = list((sandbox.home / ".config" / "opencode").glob("opencode.json.pre-permissions-*.bak"))
    assert len(backups) == 1

    assert agent_sync.claude_permissions(env) is True

    assert path.read_text(encoding="utf-8") == after_first
    backups = list((sandbox.home / ".config" / "opencode").glob("opencode.json.pre-permissions-*.bak"))
    assert len(backups) == 1  # unchanged: nothing was rewritten the 2nd time


def test_opencode_config_absent_is_a_no_op(sandbox, agent_sync, env):
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  opencode: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is True

    assert not (sandbox.home / ".config" / "opencode").exists()


# ---- Antigravity renderer: toolPermission + artifactReviewPolicy ----------

def test_antigravity_bypass_posture_is_applied(sandbox, agent_sync, env):
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  antigravity: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)
    path = _write_antigravity_config(sandbox)

    assert agent_sync.claude_permissions(env) is True

    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["toolPermission"] == "always-proceed"
    assert config["artifactReviewPolicy"] == "always-proceed"
    # Untouched: the user's own command allow-list.
    assert config["permissions"] == {"allow": ["command(ls)"]}


def test_antigravity_posture_is_idempotent(sandbox, agent_sync, env):
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  antigravity: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)
    path = _write_antigravity_config(sandbox)

    assert agent_sync.claude_permissions(env) is True
    after_first = path.read_text(encoding="utf-8")
    backups = list((sandbox.home / ".gemini" / "antigravity-cli").glob("settings.json.pre-permissions-*.bak"))
    assert len(backups) == 1

    assert agent_sync.claude_permissions(env) is True

    assert path.read_text(encoding="utf-8") == after_first
    backups = list((sandbox.home / ".gemini" / "antigravity-cli").glob("settings.json.pre-permissions-*.bak"))
    assert len(backups) == 1  # unchanged: nothing was rewritten the 2nd time


def test_antigravity_config_absent_is_a_no_op(sandbox, agent_sync, env):
    manifest = VALID_MANIFEST.replace("  claude: bypass\n", "  claude: bypass\n  antigravity: bypass\n")
    _write_manifest(sandbox, manifest)
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is True

    assert not (sandbox.home / ".gemini" / "antigravity-cli").exists()
