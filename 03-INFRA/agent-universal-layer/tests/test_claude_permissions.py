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


def test_unverified_cli_target_is_refused(sandbox, agent_sync, env):
    """Codex, OpenCode and Antigravity spell permissions differently. Accepting
    them here would emit a config that looks applied and protects nothing."""
    _write_manifest(sandbox, VALID_MANIFEST.replace("  claude: bypass", "  codex: bypass"))
    _write_settings(sandbox)

    assert agent_sync.claude_permissions(env) is False


def test_hook_file_escaping_the_permissions_dir_is_refused(sandbox, agent_sync, env):
    """The manifest is data; `file` is joined onto the vault and copied into
    the user's runtime dir, so a traversal payload must fail closed."""
    _write_manifest(sandbox, VALID_MANIFEST.replace("hooks/guardrail.mjs", "../../../../evil.mjs"))
    _write_settings(sandbox)
    before = (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8")

    assert agent_sync.claude_permissions(env) is False

    assert (sandbox.home / ".claude" / "settings.json").read_text(encoding="utf-8") == before
    assert not (sandbox.home / ".claude" / "evil.mjs").exists()


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
